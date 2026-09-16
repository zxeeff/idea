from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from idea.communication import CommunicationPolicy, CommunicationStore
from idea.domain import AgentProfile, Effort, Provider
from idea.forum import Forum, _id


class CommunicationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.workspace = Path(temporary.name)
        self.forum = Forum(self.workspace / ".idea")
        self.run_id = self.forum.create_run("Independent contributions", self.workspace)["id"]
        self.agent = self.forum.register_agent(self.run_id,
            AgentProfile("observer", Provider.OPENAI, "test-model", Effort.HIGH))
        self.store = CommunicationStore(self.forum, self.run_id)
        self.thread = self.topic()

    def topic(self, title="Evidence"):
        return self.forum.create_thread(self.run_id, "writer", title, "Initial evidence")["id"]

    def events(self, count=1, *, thread=None, at=100.0, step=0.0,
               reason="subscription", author="writer", body="Original evidence", priority=None):
        priority = priority if priority is not None else {"mention": 0, "broadcast": 1, "subscription": 2, "invitation": 3}[reason]
        mode = {"mention": "targeted", "broadcast": "broadcast"}.get(reason, "passive")
        ids = []
        with self.forum._connection() as connection:
            for index in range(count):
                created_at = datetime.fromtimestamp(at + step * index, timezone.utc).isoformat(timespec="microseconds")
                comment = _id("comment")
                connection.execute("INSERT INTO comments(id,thread_id,author,body,created_at) VALUES (?,?,?,?,?)",
                                   (comment, thread or self.thread, author, body, created_at))
                cursor = connection.execute("""INSERT INTO activity
                    (run_id,author,kind,subject_id,thread_id,notification_mode,created_at)
                    VALUES (?,?,'comment',?,?,?,?)""",
                    (self.run_id, author, comment, thread or self.thread, mode, created_at))
                event_id = int(cursor.lastrowid)
                ids.append(event_id)
                connection.execute("""INSERT INTO notification_deliveries
                    (agent_id,event_id,run_id,notification_reason,priority) VALUES (?,?,?,?,?)""",
                    (self.agent["id"], event_id, self.run_id, reason, priority))
        return ids

    def batch(self, **kwargs):
        return self.store.pending_batch(self.agent["id"], **kwargs)

    def pending_ids(self):
        with self.forum._connection() as connection:
            return [row[0] for row in connection.execute("""SELECT event_id FROM notification_deliveries
                WHERE agent_id=? AND acknowledged_at IS NULL AND withdrawn_at IS NULL ORDER BY event_id""", (self.agent["id"],))]

    def acknowledge(self, batch):
        ids = [event_id for item in batch for event_id in item.get("_delivery_event_ids", [item["id"]])]
        self.forum.acknowledge_notifications(self.agent["id"], ids)

    def test_policy_rejects_invalid_values_and_allows_zero_baseline(self):
        for kwargs in ({"debounce_seconds": -1}, {"max_wait_seconds": float("inf")},
                       {"debounce_seconds": float("nan")}, {"debounce_seconds": True},
                       {"max_wait_seconds": "5"}, {"debounce_seconds": 6, "max_wait_seconds": 5}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                CommunicationPolicy(**kwargs)
        self.assertEqual(CommunicationPolicy(0, 0), self.store.configure(debounce_seconds=0, max_wait_seconds=0))
        self.events(at=100)
        self.assertEqual(1, len(self.batch(now=100)))

    def test_partial_configuration_preserves_saved_values_and_defaults(self):
        self.assertEqual(CommunicationPolicy(), self.store.policy())
        self.store.configure(debounce_seconds=2, max_wait_seconds=8)
        reopened = CommunicationStore(Forum(self.forum.state_dir), self.run_id)
        self.assertEqual(CommunicationPolicy(2, 8), reopened.policy())
        self.assertEqual(CommunicationPolicy(2, 10), reopened.configure(max_wait_seconds=10))
        self.assertEqual(CommunicationPolicy(0, 10), reopened.configure(debounce_seconds=0))
        with self.assertRaises(ValueError):
            reopened.configure(debounce_seconds=11)
        self.assertEqual(CommunicationPolicy(0, 10), reopened.policy())
        with self.forum._connection() as connection:
            connection.execute("UPDATE communication_policies SET policy_json=? WHERE run_id=?",
                               (json.dumps({"debounce_seconds": 2}), self.run_id))
        self.assertEqual(CommunicationPolicy(2, 5), reopened.policy())

    def test_five_hundred_updates_form_one_read_only_group(self):
        ids = self.events(500, body="한국어 근거 🧪")
        before = self.pending_ids()
        batch = self.batch(now=101)
        self.assertEqual(1, len(batch))
        group = batch[0]
        self.assertEqual((ids[-1], ids[0], ids[-1], 500),
                         (group["id"], group["first_event_id"], group["through_event_id"], group["event_count"]))
        self.assertEqual(ids, group["_delivery_event_ids"])
        self.assertEqual(("thread_updates", "forum", "writer", "comment", "subscription", "passive"),
            (group["kind"], group["author"], group["latest_author"], group["latest_kind"],
             group["notification_reason"], group["notification_mode"]))
        self.assertIn("Latest original preview", group["content"])
        self.assertIn("한국어 근거 🧪", group["content"])
        self.assertEqual(before, self.pending_ids())
        self.assertEqual(batch, self.batch(now=101))

    def test_debounce_uses_latest_pending_timestamp_beyond_coverage_cutoff(self):
        self.store.configure(debounce_seconds=3, max_wait_seconds=100)
        first = self.events(1000, at=100)
        last = self.events(at=110)
        self.assertEqual([], self.batch(now=112.999))
        batch = self.batch(now=113)
        self.assertEqual(first, batch[0]["_delivery_event_ids"])
        self.assertNotIn(last[0], batch[0]["_delivery_event_ids"])

    def test_max_wait_prevents_continuous_comments_from_delaying_forever(self):
        self.store.configure(debounce_seconds=2, max_wait_seconds=5)
        first = self.events(at=100)
        second = self.events(at=104.9)
        self.assertEqual([], self.batch(now=104.999))
        batch = self.batch(now=105)
        self.assertEqual(first + second, batch[0]["_delivery_event_ids"])

    def test_pending_timing_survives_store_restart(self):
        self.store.configure(debounce_seconds=3, max_wait_seconds=7)
        ids = self.events(at=100)
        self.assertEqual([], self.batch(now=102))
        self.store = CommunicationStore(Forum(self.forum.state_dir), self.run_id)
        self.assertEqual([], self.batch(now=102.999))
        self.assertEqual(ids, self.batch(now=103)[0]["_delivery_event_ids"])

    def test_human_mentions_are_immediate_and_keep_original_event_identity(self):
        self.events(at=100)
        peer = self.events(at=100, reason="mention", author="peer")[0]
        human = self.events(at=100, reason="mention", author="human", body="Important correction")[0]
        batch = self.batch(now=100)
        self.assertEqual([human, peer], [item["id"] for item in batch[:2]])
        self.assertEqual("comment", batch[0]["kind"])
        self.assertEqual("Important correction", batch[0]["content"])
        self.assertEqual("human", batch[0]["author"])
        self.assertEqual("thread_updates", batch[2]["kind"])

    def test_each_direct_reason_and_immediate_mode_bypass_debounce(self):
        self.events(at=100)
        self.assertEqual([], self.batch(now=100))
        self.assertEqual(1, len(self.batch(now=100, immediate=True)))
        for reason in ("mention", "broadcast", "invitation"):
            with self.subTest(reason=reason):
                direct = self.events(at=100, reason=reason, author="user" if reason == "broadcast" else "peer")[0]
                batch = self.batch(now=100)
                self.assertEqual(2, len(batch))
                self.assertIn(direct, [item["id"] for item in batch])
                self.forum.acknowledge_notifications(self.agent["id"], [direct])

    def test_return_order_preserves_source_priorities_and_places_invitation_last(self):
        invitation = self.events(reason="invitation")[0]
        subscription = self.events()[0]
        broadcast = self.events(reason="broadcast")[0]
        peer = self.events(reason="mention")[0]
        human = self.events(reason="mention", author="user")[0]
        self.assertEqual([human, peer, broadcast, subscription, invitation],
                         [item["id"] for item in self.batch(now=100)])

    def test_group_ids_cover_only_pending_subscription_events_not_whole_range(self):
        first = self.events()[0]
        direct = self.events(reason="mention")[0]
        acknowledged = self.events()[0]
        withdrawn = self.events()[0]
        last = self.events()[0]
        self.forum.acknowledge_notifications(self.agent["id"], [acknowledged])
        with self.forum._connection() as connection:
            connection.execute("UPDATE notification_deliveries SET withdrawn_at='withdrawn' WHERE event_id=?", (withdrawn,))
        batch = self.batch(now=101)
        group = next(item for item in batch if item["kind"] == "thread_updates")
        self.assertEqual([first, last], group["_delivery_event_ids"])
        self.assertEqual((first, last), (group["first_event_id"], group["through_event_id"]))
        self.assertEqual([direct], [item["id"] for item in batch if item["kind"] != "thread_updates"])

    def test_cutoff_and_new_arrivals_remain_pending_after_exact_acknowledgement(self):
        ids = self.events(1001)
        batch = self.batch(now=101)
        newer = self.events(at=101)
        self.acknowledge(batch)
        self.assertEqual([ids[-1], newer[0]], self.pending_ids())
        remaining = self.batch(now=102)
        self.assertEqual([ids[-1], newer[0]], remaining[0]["_delivery_event_ids"])

    def test_oldest_threads_share_event_budget_before_one_thread_takes_more(self):
        flood = self.events(4000)
        small = [self.events(thread=self.topic())[0] for _ in range(24)]
        first = self.batch(now=101)
        self.assertEqual(20, len(first))
        self.assertEqual(1000, sum(item["event_count"] for item in first))
        flood_group = next(item for item in first if item["thread_id"] == self.thread)
        self.assertEqual(flood[:981], flood_group["_delivery_event_ids"])
        self.assertEqual(set(small[:19]), {item["id"] for item in first if item["thread_id"] != self.thread})
        self.acknowledge(first)
        second = self.batch(now=101)
        self.assertTrue(set(small[19:]).issubset({item["id"] for item in second}))

    def test_batch_units_and_unicode_previews_are_bounded(self):
        body = "한글🙂" * 2000
        self.events(25, reason="mention", body=body)
        for _ in range(25):
            self.events(thread=self.topic("큰 제목🙂" * 200), body=body)
        batch = self.batch(now=101)
        direct = [item for item in batch if item["kind"] != "thread_updates"]
        groups = [item for item in batch if item["kind"] == "thread_updates"]
        self.assertEqual((40, 20, 20), (len(batch), len(direct), len(groups)))
        self.assertEqual(50, len(self.pending_ids()))
        for item in batch:
            self.assertTrue(item["content_truncated"])
            self.assertLessEqual(len(item["content"]), 2100)
            self.assertLessEqual(len(item["thread_title"]), 240)
            item["content"].encode("utf-8").decode("utf-8")

    def test_reader_snapshot_excludes_write_during_preview_construction(self):
        direct = self.events(reason="mention")[0]
        original = self.events()[0]
        preview = self.forum._activity_previews
        added = []

        def concurrent_write(connection, items):
            if not added:
                added.extend(self.events(at=101))
            return preview(connection, items)

        with patch.object(self.forum, "_activity_previews", side_effect=concurrent_write):
            batch = self.batch(now=101)
        group = next(item for item in batch if item["kind"] == "thread_updates")
        self.assertEqual([original], group["_delivery_event_ids"])
        self.acknowledge(batch)
        self.assertEqual(added, self.pending_ids())

    def test_run_scope_and_invalid_clock_are_rejected_without_writes(self):
        other_run = self.forum.create_run("Other objective", self.workspace)["id"]
        other = CommunicationStore(self.forum, other_run)
        with self.assertRaises(KeyError):
            other.pending_batch(self.agent["id"], now=101)
        for value in (True, float("nan"), float("inf"), "100"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.batch(now=value)
        self.assertEqual(CommunicationPolicy(), self.store.policy())

    def test_representative_provenance_belongs_to_latest_original_event(self):
        original = self.events()[0]
        self.forum.subscribe(self.agent["id"], self.thread, wake=True)
        timestamp = datetime.fromtimestamp(100, timezone.utc).isoformat(timespec="microseconds")
        with patch("idea.forum._now", return_value=timestamp):
            self.forum.add_comment(self.thread, "earlier-author", "Related observation",
                                   reply_to_event_id=original, relation="supports")
            latest = self.forum.add_comment(self.thread, "reviewer", "Reported independent check",
                reply_to_event_id=original, relation="verifies", validation="검증 상세 " * 1000)
        group = self.batch(now=101)[0]
        self.assertEqual(latest["event_id"], group["id"])
        self.assertEqual("reviewer", group["latest_author"])
        self.assertEqual("verifies", group["provenance"]["relation"])
        self.assertTrue(group["provenance"]["validation_truncated"])
        self.assertLessEqual(len(group["provenance"]["validation"]), 512)
        self.assertEqual({"supports": 1, "verifies": 1}, group["relation_counts"])

    def test_relation_counts_and_bounded_objection_refs_use_only_covered_ids(self):
        ids = self.events(1001)
        labels = {index: "challenges" for index in range(1, 11)}
        labels.update({11: "retracts", 12: "supersedes", 13: "supports",
                       14: "reply", 999: "verifies", 1000: "challenges"})
        with self.forum._connection() as connection:
            for index, relation in labels.items():
                connection.execute("""INSERT INTO comment_provenance
                    (comment_id,event_id,run_id,reply_to_event_id,relation,validation)
                    SELECT subject_id,id,run_id,?,?,'Author-reported checks' FROM activity WHERE id=?""",
                    (ids[0], relation, ids[index]))
        group = self.batch(now=101)[0]
        self.assertEqual(ids[:1000], group["_delivery_event_ids"])
        self.assertEqual({"challenges": 10, "retracts": 1, "supersedes": 1,
                          "supports": 1, "verifies": 1}, group["relation_counts"])
        self.assertEqual([{"event_id": event_id, "relation": "challenges"} for event_id in ids[1:9]],
                         group["relation_events"])
        self.assertEqual("verifies", group["provenance"]["relation"])
        self.acknowledge([group])
        last = self.batch(now=101)[0]
        self.assertEqual({"challenges": 1}, last["relation_counts"])
        self.assertEqual([{"event_id": ids[-1], "relation": "challenges"}], last["relation_events"])


if __name__ == "__main__":
    unittest.main()
