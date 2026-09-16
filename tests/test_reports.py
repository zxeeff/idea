from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from idea.approaches import ApproachStore
from idea.domain import AgentProfile, Effort, Provider
from idea.forum import Forum
from idea.reports import ReportStore, metadata_for_events
from idea.workspaces import WorkspaceStore


class ReportsTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        (self.root / "sample.txt").write_text("base\n")
        self.forum = Forum(self.root / ".idea")
        self.run = self.forum.create_run("Compare independent approaches", self.root)
        self.approaches = ApproachStore(self.forum, self.run["id"])
        self.store = ReportStore(self.forum, self.run["id"])
        self.source = self.topic("Source")
        self.target = self.topic("Target")

    def topic(self, title):
        thread = self.forum.create_thread(self.run["id"], "writer", title, "Original evidence")
        approach = self.approaches.create(thread["id"], "writer", hypothesis="Independent hypothesis", next_check="Compare sources")
        return thread | {"approach_id": approach["id"]}

    def peer(self, name):
        return self.forum.register_agent(self.run["id"], AgentProfile(name, Provider.OPENAI, "fake", Effort.LOW))

    def publish(self, **kwargs):
        fields = {"summary": "A provisional result", "conditions": "Only under the measured conditions",
                  "open_questions": "Does it generalize?", "source_event_ids": [self.source["event_id"]]}
        fields.update(kwargs)
        return self.store.publish(self.source["approach_id"], "writer", **fields)

    def counts(self):
        with self.forum._connection() as connection:
            return tuple(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in (
                "comments", "activity", "notification_deliveries", "comment_provenance", "comment_evidence",
                "approach_reports", "report_event_references", "report_exchanges",
            ))

    def test_invalid_sources_and_validation_references_leave_no_partial_records(self):
        other_run = self.forum.create_run("Foreign", self.root)
        foreign = self.forum.create_thread(other_run["id"], "writer", "Other", "Unrelated")
        before = self.counts()
        for fields in (
            {"source_event_ids": []}, {"source_event_ids": [self.target["event_id"]]},
            {"source_event_ids": [self.source["event_id"], foreign["event_id"]]},
            {"source_event_ids": [2 ** 62]}, {"source_event_ids": [True]},
            {"source_event_ids": [self.source["event_id"]] * 17},
            {"validation_event_ids": [self.source["event_id"]]},
            {"validation_event_ids": [foreign["event_id"]]},
        ):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                self.publish(**fields)
            self.assertEqual(before, self.counts())

    def test_publish_and_exchange_rollback_comment_notifications_and_links_together(self):
        observer = self.peer("observer")
        self.forum.subscribe(observer["id"], self.source["id"], wake=True)
        self.forum.subscribe(observer["id"], self.target["id"], wake=True)
        before = self.counts()
        with self.forum._connection() as connection:
            connection.execute("CREATE TRIGGER reject_report BEFORE INSERT ON approach_reports BEGIN SELECT RAISE(ABORT,'report failure'); END")
        with self.assertRaisesRegex(sqlite3.IntegrityError, "report failure"):
            self.publish()
        self.assertEqual(before, self.counts())
        with self.forum._connection() as connection:
            connection.execute("DROP TRIGGER reject_report")
        report = self.publish()
        before_exchange = self.counts()
        with self.forum._connection() as connection:
            connection.execute("CREATE TRIGGER reject_exchange BEFORE INSERT ON report_exchanges BEGIN SELECT RAISE(ABORT,'exchange failure'); END")
        with self.assertRaisesRegex(sqlite3.IntegrityError, "exchange failure"):
            self.store.adopt(report["id"], self.target["approach_id"], "reader", application="Try the same check")
        self.assertEqual(before_exchange, self.counts())

    def test_artifact_validation_must_match_immutable_version_and_remains_reported(self):
        worker = self.peer("worker")
        copies = WorkspaceStore(self.forum, self.run["id"])
        copies.configure("isolated")
        private = copies.prepare(worker["id"])
        (private / "sample.txt").write_text("first contribution\n")
        first = copies.publish(worker["id"])
        (private / "sample.txt").write_text("second contribution\n")
        second = copies.publish(worker["id"])
        verification = self.forum.add_comment(self.source["id"], "reviewer", "Reported check",
            reply_to_event_id=self.source["event_id"], relation="verifies", artifact_id=first["id"], validation="Ran one local check")
        before = self.counts()
        with self.assertRaisesRegex(ValueError, "different artifact"):
            self.publish(artifact_id=second["id"], validation_event_ids=[verification["event_id"]])
        self.assertEqual(before, self.counts())
        report = self.publish(artifact_id=first["id"], validation_event_ids=[verification["event_id"]])
        self.assertEqual({"id": first["id"], "base_revision": first["base_revision"], "patch_sha256": first["patch_sha256"]}, report["artifact"])
        self.assertEqual("verifies", report["validation_events"][0]["relation"])
        self.assertNotIn("patch_path", json.dumps(report))
        self.assertNotIn("verified", report)
        retraction = self.forum.add_comment(self.source["id"], "reviewer", "The test was insufficient",
            reply_to_event_id=verification["event_id"], relation="retracts")
        changed = self.store.get(report["id"])
        self.assertEqual(report["artifact"], changed["artifact"])
        self.assertEqual([verification["event_id"]], changed["validation_event_ids"])
        self.assertEqual([retraction["event_id"]], [item["event_id"] for item in changed["source_changes"]["items"]])

    def test_concurrent_adoption_is_one_public_exchange_and_wakes_only_target_subscribers(self):
        origin, target, outsider = (self.peer(name) for name in ("origin-reader", "target-reader", "outsider"))
        self.forum.subscribe(origin["id"], self.source["id"], wake=True)
        self.forum.subscribe(target["id"], self.target["id"], wake=True)
        report = self.publish(summary="@all @outsider A provisional result")
        origin_events = self.forum.pending_notifications(origin["id"])
        self.assertEqual([report["event_id"]], [item["id"] for item in origin_events])
        self.assertEqual([], self.forum.pending_notifications(outsider["id"]))
        self.forum.acknowledge_notifications(origin["id"], [report["event_id"]])
        application = "@all @outsider Reuse only after checking the conditions"
        with ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(lambda _: self.store.adopt(report["id"], self.target["approach_id"], "reader", application=application), range(8)))
        self.assertEqual(1, len({item["id"] for item in results}))
        exchange = results[0]
        self.assertEqual(report, exchange["source_report"])
        posted = self.forum.get_comment(self.target["id"], exchange["comment_id"])
        self.assertEqual(report["event_id"], posted["provenance"]["reply_to_event_id"])
        self.assertEqual("supports", posted["provenance"]["relation"])
        self.assertIn(report["conditions"], posted["body"])
        self.assertIn(report["open_questions"], posted["body"])
        self.assertEqual([exchange["event_id"]], [item["id"] for item in self.forum.pending_notifications(target["id"])])
        self.assertEqual([], self.forum.pending_notifications(origin["id"]))
        self.assertEqual([], self.forum.pending_notifications(outsider["id"]))
        before = self.counts()
        with self.assertRaisesRegex(ValueError, "different application"):
            self.store.adopt(report["id"], self.target["approach_id"], "reader", application="Changed interpretation")
        self.assertEqual(before, self.counts())

    def test_old_and_new_objections_and_successor_versions_remain_visible(self):
        early = self.forum.add_comment(self.source["id"], "reviewer", "Known limitation",
            reply_to_event_id=self.source["event_id"], relation="challenges")
        original = self.publish()
        original_body = self.forum.get_comment(self.source["id"], original["comment_id"])["body"]
        objections = [early]
        for number in range(10):
            objections.append(self.forum.add_comment(self.source["id"], "reviewer", f"Objection {number}",
                reply_to_event_id=original["event_id"] if number % 2 else self.source["event_id"], relation="challenges"))
        before = self.counts()
        with self.assertRaisesRegex(ValueError, "same author's"):
            self.store.publish(self.source["approach_id"], "reader", summary="Overwrite", conditions="No conditions",
                source_event_ids=[self.source["event_id"]], supersedes_report_id=original["id"])
        self.assertEqual(before, self.counts())
        replacement = self.publish(summary="Revised interpretation", supersedes_report_id=original["id"])
        value = self.store.get(original["id"])
        self.assertEqual(original["summary"], value["summary"])
        self.assertEqual(original_body, self.forum.get_comment(self.source["id"], original["comment_id"])["body"])
        self.assertTrue(value["is_superseded"])
        self.assertEqual(replacement["id"], value["superseded_by"]["items"][0]["id"])
        self.assertEqual(11, value["source_changes"]["total_count"])
        expected = [item["event_id"] for item in objections[:4] + objections[-4:]]
        self.assertEqual(expected, [item["event_id"] for item in value["source_changes"]["items"]])
        self.assertFalse(value["source_changes"]["items"][0]["after_report"])
        self.assertTrue(value["source_changes"]["items"][-1]["after_report"])

    def test_unicode_previews_pagination_and_metadata_reads_are_bounded_and_read_only(self):
        reports = [self.publish(summary="증거" * 1000 + f" {number}%_literal", conditions="조건" * 1000,
                                open_questions="미해결" * 300) for number in range(3)]
        first = self.store.list(limit=1, query="%_literal", thread_id=self.source["id"])
        self.assertEqual(reports[0]["id"], first["items"][0]["id"])
        for name in ("summary", "conditions", "open_questions"):
            self.assertLessEqual(len(first["items"][0][name].encode("utf-8")), 512)
            self.assertTrue(first["items"][0][f"{name}_truncated"])
        rest = self.store.list(limit=2, after=first["next_cursor"], query="%_literal")
        self.assertEqual([item["id"] for item in reports[1:]], [item["id"] for item in rest["items"]])
        self.assertIsNone(rest["next_cursor"])
        self.assertEqual([], self.store.list(query="%_missing")["items"])
        self.assertEqual(reports[0]["conditions"], self.store.get(reports[0]["id"])["conditions"])
        exchange = self.store.adopt(reports[0]["id"], self.target["approach_id"], "reader", application="적용" * 1000)
        before = self.counts()
        with self.forum._connection() as connection:
            connection.execute("BEGIN")
            statements = []
            connection.set_trace_callback(statements.append)
            single = metadata_for_events(connection, [reports[0]["event_id"]])
            single_count = len(statements)
            statements.clear()
            metadata_for_events(connection, [item["event_id"] for item in reports])
            self.assertEqual(single_count, len(statements))
            delivered = metadata_for_events(connection, [exchange["event_id"]])[exchange["event_id"]]["exchange"]
            self.assertLessEqual(len(delivered["application"].encode("utf-8")), 512)
            self.assertTrue(delivered["application_truncated"])
            self.assertTrue(single[reports[0]["event_id"]]["report"]["conditions_truncated"])
            self.assertEqual(0, connection.total_changes)
        self.assertEqual(before, self.counts())


if __name__ == "__main__":
    unittest.main()
