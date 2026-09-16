from __future__ import annotations

import json
import tempfile
import unittest
from itertools import repeat
from pathlib import Path
from unittest.mock import patch

from idea import provenance
from idea.commands import dispatch_forum
from idea.domain import AgentProfile, Effort, Provider
from idea.forum import Forum
from idea.workspaces import WorkspaceStore


class ProvenanceTest(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.workspace = Path(directory.name)
        (self.workspace / "sample.txt").write_text("base\n")
        self.forum = Forum(self.workspace / ".idea")
        self.run = self.forum.create_run("Compare evidence", self.workspace)
        self.peer = self.forum.register_agent(
            self.run["id"], AgentProfile("reader", Provider.OPENAI, "fake", Effort.LOW)
        )
        self.topic = self.forum.create_thread(self.run["id"], "writer", "Claim", "Original claim")
        self.other_topic = self.forum.create_thread(self.run["id"], "human", "Evidence", "Independent evidence")
        other_run = self.forum.create_run("Unrelated", self.workspace)
        self.foreign_topic = self.forum.create_thread(other_run["id"], "writer", "Other run", "Unrelated")

    def counts(self) -> tuple[int, ...]:
        with self.forum._connection() as connection:
            return tuple(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                         for table in ("comments", "activity", "notification_deliveries",
                                       "comment_provenance", "comment_evidence"))

    def comment(self, **metadata):
        return self.forum.add_comment(self.topic["id"], "writer", "@reader response", **metadata)

    def test_cross_thread_references_round_trip_without_copying_source_bodies(self):
        result = self.comment(reply_to_event_id=self.topic["event_id"], relation="supports",
                              evidence_event_ids=[self.other_topic["event_id"]])
        expected = result["provenance"]
        self.assertEqual(self.topic["event_id"], expected["reply_to_event_id"])
        self.assertEqual({"event_id": self.topic["event_id"], "thread_id": self.topic["id"],
                          "subject_id": self.topic["id"], "kind": "thread", "author": "writer"},
                         expected["reply_to_event"])
        self.assertEqual(self.other_topic["id"], expected["evidence_events"][0]["thread_id"])
        self.assertNotIn("body", expected["evidence_events"][0])
        for record in (
            self.forum.get_comment(self.topic["id"], result["id"]),
            self.forum.get_thread(self.topic["id"])["comments"][0],
            self.forum.comments_page(self.topic["id"])["items"][0],
        ):
            self.assertEqual(result["event_id"], record["event_id"])
            self.assertEqual(expected, record["provenance"])
        pending = self.forum.pending_notifications(self.peer["id"])[0]
        self.assertEqual(result["event_id"], pending["event_id"])
        self.assertEqual("supports", pending["provenance"]["relation"])

    def test_artifact_reference_snapshots_version_and_remains_an_author_report(self):
        copies = WorkspaceStore(self.forum, self.run["id"])
        copies.configure("isolated")
        private = copies.prepare(self.peer["id"])
        (private / "sample.txt").write_text("contribution\n")
        artifact = copies.publish(self.peer["id"], validation="Writer's report")
        result = self.comment(reply_to_event_id=self.topic["event_id"], relation="verifies",
                              artifact_id=artifact["id"], validation="Ran the listed command; result was 0")
        metadata = result["provenance"]
        self.assertEqual({"id": artifact["id"], "base_revision": artifact["base_revision"],
                          "patch_sha256": artifact["patch_sha256"]}, metadata["artifact"])
        self.assertNotIn("patch_path", json.dumps(metadata))
        self.assertNotIn("verified", metadata)
        self.assertNotIn("verdict", metadata)
        before = self.counts()
        with self.assertRaisesRegex(ValueError, "another run"):
            self.forum.add_comment(self.foreign_topic["id"], "writer", "Wrong artifact",
                                   artifact_id=artifact["id"])
        self.assertEqual(before, self.counts())

    def test_invalid_links_are_rejected_before_any_post_or_notification(self):
        parent = self.topic["event_id"]
        cases = [
            {"reply_to_event_id": self.foreign_topic["event_id"]},
            {"evidence_event_ids": [self.foreign_topic["event_id"]]},
            {"reply_to_event_id": 9_223_372_036_854_775_807},
            {"reply_to_event_id": 0}, {"reply_to_event_id": -1},
            {"reply_to_event_id": True}, {"reply_to_event_id": str(parent)},
            {"reply_to_event_id": 2 ** 63}, {"relation": "proves"},
            {"relation": "supports"}, {"relation": []},
            {"reply_to_event_id": parent, "relation": "verifies", "validation": " \n"},
            {"validation": None}, {"validation": "x" * (32 * 1024 + 1)},
            {"evidence_event_ids": [parent] * 17}, {"evidence_event_ids": repeat(parent)},
            {"evidence_event_ids": [False]}, {"evidence_event_ids": [parent + 0.5]},
            {"evidence_event_ids": "1"}, {"evidence_event_ids": None},
            {"artifact_id": "missing"}, {"artifact_id": False},
        ]
        before = self.counts()
        for metadata in cases:
            with self.subTest(metadata=metadata), self.assertRaises(ValueError):
                self.comment(**metadata)
            self.assertEqual(before, self.counts())
        # Even the next autoincrement ID cannot be cited before it exists.
        with self.forum._connection() as connection:
            next_id = connection.execute("SELECT MAX(id) + 1 FROM activity").fetchone()[0]
        with self.assertRaisesRegex(ValueError, "unknown referenced event"):
            self.comment(evidence_event_ids=[next_id])
        self.assertEqual(before, self.counts())

    def test_retractions_and_replacements_are_owned_append_only_statements(self):
        for relation in ("retracts", "supersedes"):
            with self.subTest(relation=relation):
                with self.assertRaisesRegex(ValueError, "original author"):
                    self.forum.add_comment(self.topic["id"], "reader", "Replace someone else's claim",
                                           reply_to_event_id=self.topic["event_id"], relation=relation)
                own = self.forum.add_comment(self.topic["id"], "WRITER", "Changed my view",
                                             reply_to_event_id=self.topic["event_id"], relation=relation)
                self.assertEqual(relation, own["provenance"]["relation"])
        challenge = self.forum.add_comment(self.topic["id"], "reader", "Independent disagreement",
                                           reply_to_event_id=self.topic["event_id"], relation="challenges")
        self.assertEqual("challenges", challenge["provenance"]["relation"])
        self.assertEqual("Original claim", self.forum.get_thread(self.topic["id"])["body"])

    def test_links_post_event_and_recipient_snapshot_share_one_transaction(self):
        before = self.counts()
        original = provenance.record

        def record_then_fail(*args, **kwargs):
            original(*args, **kwargs)
            raise RuntimeError("storage failure after links")

        with patch.object(provenance, "record", side_effect=record_then_fail):
            with self.assertRaisesRegex(RuntimeError, "after links"):
                self.comment(reply_to_event_id=self.topic["event_id"],
                             evidence_event_ids=[self.other_topic["event_id"]])
        self.assertEqual(before, self.counts())
        self.assertEqual([], self.forum.pending_notifications(self.peer["id"]))

    def test_reply_trigger_records_resolved_event_and_rejects_conflicting_parent(self):
        mention = self.forum.add_comment(self.other_topic["id"], "human", "@reader Please check")
        arguments = dict(forum=self.forum, run_id=self.run["id"], agent_id=self.peer["id"])
        before = self.counts()
        for invalid_parent in (self.topic["event_id"], str(mention["event_id"]), True):
            with self.assertRaisesRegex(ValueError, "disagrees"):
                dispatch_forum(**arguments, command="reply-trigger", payload={
                    "event": mention["event_id"], "body": "Wrong linkage",
                    "reply_to_event_id": invalid_parent,
                })
        self.assertEqual(before, self.counts())
        response = dispatch_forum(**arguments, command="reply-trigger", payload={
            "event": mention["event_id"], "body": "Checks complete", "relation": "verifies",
            "validation": "Reviewed the exact cited source", "evidence_event_ids": [self.topic["event_id"]],
            "author": "writer",  # Payload cannot replace the bound peer's name.
        })
        self.assertEqual("reader", response["author"])
        self.assertEqual(self.other_topic["id"], response["thread_id"])
        self.assertEqual(mention["event_id"], response["provenance"]["reply_to_event_id"])
        self.assertEqual(mention["id"], response["provenance"]["reply_to_event"]["subject_id"])

    def test_peer_discovery_and_reply_command_accept_optional_provenance(self):
        arguments = dict(forum=self.forum, run_id=self.run["id"], agent_id=self.peer["id"])
        recent = dispatch_forum(**arguments, command="recent", payload={})
        self.assertEqual({self.topic["id"], self.other_topic["id"]}, {item["id"] for item in recent["items"]})
        value = dispatch_forum(**arguments, command="reply", payload={
            "thread_id": self.topic["id"], "body": "I disagree", "relation": "challenges",
            "reply_to_event_id": self.topic["event_id"], "evidence_event_ids": [self.other_topic["event_id"]],
        })
        self.assertEqual("challenges", value["provenance"]["relation"])
        for invalid_event in (True, "1", -1, 2 ** 63):
            with self.assertRaises(ValueError):
                dispatch_forum(**arguments, command="reply-trigger", payload={"event": invalid_event, "body": "Reply"})

    def test_legacy_comments_and_database_upgrade_preserve_history_and_layout(self):
        old = self.comment()
        pending_before = self.forum.notification_counts(self.run["id"])
        with self.forum._connection() as connection:
            connection.execute("DROP TABLE comment_evidence")
            connection.execute("DROP TABLE comment_provenance")
            connection.execute("INSERT INTO comments VALUES (?, ?, ?, ?, ?)",
                               ("legacy-no-event", self.topic["id"], "human", "Very old comment", "2000"))
        reopened = Forum(self.forum.state_dir)
        self.assertEqual(pending_before, reopened.notification_counts(self.run["id"]))
        legacy = reopened.get_comment(self.topic["id"], old["id"])
        self.assertEqual(old["event_id"], legacy["event_id"])
        self.assertNotIn("provenance", legacy)
        self.assertIsNone(reopened.get_comment(self.topic["id"], "legacy-no-event")["event_id"])
        with reopened._connection() as connection:
            columns = [row["name"] for row in connection.execute("PRAGMA table_info(comments)")]
        self.assertEqual(["id", "thread_id", "author", "body", "created_at"], columns)
        linked = reopened.add_comment(self.topic["id"], "reader", "Later response",
                                     reply_to_event_id=old["event_id"])
        self.assertEqual(old["id"], linked["provenance"]["reply_to_event"]["subject_id"])

    def test_thread_changes_freezes_pages_and_never_acknowledges_delivery(self):
        comments = [self.comment(reply_to_event_id=self.topic["event_id"]) for _ in range(3)]
        first = self.forum.thread_changes(self.topic["id"], limit=2)
        self.assertEqual([self.topic["event_id"], comments[0]["event_id"]],
                         [item["id"] for item in first["items"]])
        self.assertTrue(first["has_more"])
        later = self.comment()
        second = self.forum.thread_changes(self.topic["id"], after_event=first["next_cursor"],
                                           through_event=first["through_event"], limit=2)
        self.assertEqual([item["event_id"] for item in comments[1:]],
                         [item["id"] for item in second["items"]])
        self.assertEqual(first["through_event"], second["through_event"])
        self.assertIsNone(second["next_cursor"])
        self.assertFalse(second["has_more"])
        self.assertEqual("preview", second["content_format"])
        next_interval = self.forum.thread_changes(self.topic["id"], after_event=second["through_event"])
        self.assertEqual([later["event_id"]], [item["id"] for item in next_interval["items"]])
        self.assertEqual(4, len(self.forum.pending_notifications(self.peer["id"])))
        self.assertEqual(0, self.forum.get_agent(self.peer["id"])["last_activity_id"])

    def test_changes_input_bounds_and_command_run_scope(self):
        for kwargs in ({"after_event": True}, {"after_event": -1}, {"after_event": "0"},
                       {"through_event": False}, {"through_event": 2 ** 63},
                       {"limit": 101}, {"limit": 0}, {"limit": 1.5}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.forum.thread_changes(self.topic["id"], **kwargs)
        first = self.forum.thread_changes(self.topic["id"], through_event=2 ** 62)
        self.assertLess(first["through_event"], 2 ** 62)
        later = self.comment()
        frozen = self.forum.thread_changes(self.topic["id"], through_event=first["through_event"])
        self.assertNotIn(later["event_id"], [item["id"] for item in frozen["items"]])
        with self.assertRaisesRegex(ValueError, "another run"):
            dispatch_forum(self.forum, self.run["id"], self.peer["id"], "changes",
                           {"thread_id": self.foreign_topic["id"]})
        value = dispatch_forum(self.forum, self.run["id"], self.peer["id"], "changes",
                               {"thread_id": self.topic["id"], "after_event": self.topic["event_id"]})
        self.assertEqual([later["event_id"]], [item["id"] for item in value["items"]])

    def test_bounded_preview_and_batched_comment_provenance_queries(self):
        long_validation = "reported details; " * 500
        for _ in range(30):
            self.forum.add_comment(self.topic["id"], "writer", "@reader " + "x" * 4000,
                                   reply_to_event_id=self.topic["event_id"], relation="verifies",
                                   evidence_event_ids=[self.other_topic["event_id"]], validation=long_validation)
        events = self.forum.pending_notifications(self.peer["id"])
        self.assertEqual(2000, len(events[0]["content"]))
        self.assertTrue(events[0]["content_truncated"])
        self.assertEqual(512, len(events[0]["provenance"]["validation"]))
        self.assertTrue(events[0]["provenance"]["validation_truncated"])
        self.assertEqual(long_validation, self.forum.comments_page(self.topic["id"], limit=1)["items"][0]
                         ["provenance"]["validation"])
        original = self.forum._connect
        selects = []

        def traced_connect():
            connection = original()
            connection.set_trace_callback(lambda sql: selects.append(sql) if sql.lstrip().upper().startswith("SELECT") else None)
            return connection

        with patch.object(self.forum, "_connect", side_effect=traced_connect):
            self.forum.comments_page(self.topic["id"], limit=1)
            small_count = len(selects)
            selects.clear()
            self.forum.comments_page(self.topic["id"], limit=30)
        self.assertEqual(small_count, len(selects))
        self.assertLess(small_count, 10)


if __name__ == "__main__":
    unittest.main()
