from __future__ import annotations

import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from idea.domain import AgentProfile, Effort, Provider
from idea.forum import Forum


class AttentionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.forum = Forum(self.root / ".idea")
        self.run = self.forum.create_run("Compare independent approaches", self.root)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def peer(self, name: str) -> dict:
        return self.forum.register_agent(
            self.run["id"], AgentProfile(name, Provider.OPENAI, "test-model", Effort.HIGH)
        )

    def thread(self, body: str = "Public evidence", *, author: str = "human") -> dict:
        return self.forum.create_thread(self.run["id"], author, "Discussion", body)

    def test_subscriptions_are_digest_by_default_and_wake_only_when_requested(self) -> None:
        peer = self.peer("reader")
        topic = self.thread()
        self.assertFalse(self.forum.subscribe(peer["id"], topic["id"])["wake"])
        comment = self.forum.add_comment(topic["id"], "other", "Useful update")
        following = self.forum.activity_page(peer["id"])
        self.assertIn(comment["id"], [event["subject_id"] for event in following["items"]])
        self.assertEqual([], self.forum.pending_notifications(peer["id"]))

        self.forum.subscribe(peer["id"], topic["id"], wake=True)
        wake = self.forum.add_comment(topic["id"], "other", "Please consider this evidence")
        self.forum.add_comment(topic["id"], "READER", "My own update")
        pending = self.forum.pending_notifications(peer["id"])
        self.assertEqual([wake["id"]], [event["subject_id"] for event in pending])
        self.assertEqual("subscription", pending[0]["notification_reason"])
        direct = self.forum.add_comment(topic["id"], "other", "@reader direct question")
        pending = self.forum.pending_notifications(peer["id"])
        self.assertEqual(direct["id"], pending[0]["subject_id"])
        self.assertEqual("mention", pending[0]["notification_reason"])
        self.assertEqual(2, len(pending))  # Mention + subscription creates one delivery.
        self.forum.unsubscribe(peer["id"], topic["id"])
        self.forum.add_comment(topic["id"], "other", "No new subscription wake")
        self.assertEqual(2, len(self.forum.pending_notifications(peer["id"])))
        self.assertFalse(self.forum.unsubscribe(peer["id"], topic["id"])["removed"])

    def test_public_discovery_includes_mentions_to_other_peers(self) -> None:
        reader = self.peer("reader")
        self.peer("reader-2")
        target = self.thread("@reader-2 exact recipient")
        self.thread("@reader-20 unknown recipient")
        own = self.thread("My note", author="reader")
        self.assertEqual([], self.forum.activity_page(reader["id"])["items"])
        public = self.forum.activity_page(reader["id"], scope="all")
        subjects = [event["subject_id"] for event in public["items"]]
        self.assertIn(target["id"], subjects)
        self.assertNotIn(own["id"], subjects)
        self.assertEqual([], self.forum.pending_notifications(reader["id"]))
        self.assertEqual("@reader-2 exact recipient", self.forum.get_thread(target["id"])["body"])

    def test_feed_pagination_bounds_payload_and_does_not_ack_delivery(self) -> None:
        peer = self.peer("reader")
        topics = [self.thread("@reader " + "x" * 5000) for _ in range(7)]
        first = self.forum.activity_page(peer["id"], limit=2)
        self.assertTrue(first["has_more"])
        self.assertEqual(first["items"][-1]["id"], first["through_id"])
        self.assertEqual(2000, len(first["items"][0]["content"]))
        self.assertTrue(first["items"][0]["content_truncated"])
        self.assertNotIn("audience_json", first["items"][0])
        self.forum.mark_activity_seen(peer["id"], first["through_id"])
        second = self.forum.activity_page(peer["id"], limit=2)
        self.assertEqual(topics[2]["id"], second["items"][0]["subject_id"])
        last = self.forum.activity_page(peer["id"], after=second["next_cursor"], limit=10)
        self.assertFalse(last["has_more"])
        self.assertEqual(3, len(last["items"]))
        self.assertEqual(7, len(self.forum.pending_notifications(peer["id"])))
        self.assertLessEqual(len(self.forum.pending_notifications(peer["id"])[0]["content"]), 2000)

    def test_runtime_ack_is_independent_and_can_share_a_transaction(self) -> None:
        peer = self.peer("reader")
        self.thread("@reader one")
        first = self.forum.pending_notifications(peer["id"])[0]["id"]
        self.forum.mark_wake_scanned(peer["id"], first)
        self.forum.mark_activity_seen(peer["id"], first)
        self.assertEqual([first], [item["id"] for item in self.forum.pending_notifications(peer["id"])])
        with self.assertRaisesRegex(RuntimeError, "rollback"):
            with self.forum._connection() as connection:
                self.forum.acknowledge_notifications(peer["id"], [first], connection=connection)
                raise RuntimeError("rollback")
        self.assertEqual(1, self.forum.notification_counts(self.run["id"])["pending_events"])
        self.thread("@reader two arrives during execution")
        second = max(item["id"] for item in self.forum.pending_notifications(peer["id"]))
        self.forum.acknowledge_notifications(peer["id"], [first])
        self.forum.acknowledge_notifications(peer["id"], [first])
        self.assertEqual([second], [item["id"] for item in self.forum.pending_notifications(peer["id"])])
        queued = self.forum.pending_notification_agents(self.run["id"])[0]
        self.assertEqual((second, second), (queued["first_event_id"], queued["last_event_id"]))

    def test_event_and_recipient_delivery_commit_atomically(self) -> None:
        peer = self.peer("reader")
        original = self.forum._record_activity

        def fail_after_delivery(*args, **kwargs):
            original(*args, **kwargs)
            raise RuntimeError("delivery transaction interrupted")

        with patch.object(self.forum, "_record_activity", side_effect=fail_after_delivery):
            with self.assertRaises(RuntimeError):
                self.thread("@reader should roll back")
        self.assertEqual([], self.forum.list_threads(self.run["id"]))
        self.assertEqual([], self.forum.pending_notifications(peer["id"]))
        self.assertEqual(0, self.forum.activity_high_water(self.run["id"]))

    def test_500_peer_broadcast_is_a_snapshot_and_never_self_notifies(self) -> None:
        peers = [self.peer(f"peer-{number}") for number in range(500)]
        self.forum.retire_agent(peers[-1]["id"], "Finished")
        self.thread("@all Please read when available", author="peer-0")
        self.assertEqual({"pending_events": 498, "pending_agents": 498},
                         self.forum.notification_counts(self.run["id"]))
        self.assertEqual(498, len(self.forum.pending_notification_agents(self.run["id"])))
        late = self.peer("late-reader")
        self.assertEqual([], self.forum.pending_notifications(late["id"]))
        self.assertEqual([], self.forum.activity_page(late["id"], after=0)["items"])
        self.assertEqual([], self.forum.pending_notifications(peers[0]["id"]))
        self.assertEqual([], self.forum.pending_notifications(peers[-1]["id"]))
        self.assertEqual(1, len(self.forum.pending_notifications(peers[1]["id"])))

    def test_concurrent_posters_keep_one_recipient_record_per_event(self) -> None:
        peer = self.peer("reader")
        topic = self.thread()
        self.forum.subscribe(peer["id"], topic["id"], wake=True)
        with ThreadPoolExecutor(max_workers=8) as executor:
            comments = list(executor.map(
                lambda number: self.forum.add_comment(topic["id"], f"writer-{number}", "@reader update"),
                range(32),
            ))
        pending = self.forum.pending_notifications(peer["id"], limit=100)
        self.assertEqual({comment["id"] for comment in comments},
                         {event["subject_id"] for event in pending})
        self.assertEqual(32, self.forum.notification_counts(self.run["id"])["pending_events"])

    def test_legacy_migration_skips_consumed_history_and_is_not_replayed(self) -> None:
        peer = self.peer("reader")
        self.thread("@reader consumed")
        self.forum.mark_wake_scanned(peer["id"], self.forum.activity_high_water(self.run["id"]))
        self.thread("@all pending legacy broadcast")
        expected = self.forum.activity_high_water(self.run["id"])
        late = self.peer("late-reader")
        retired = self.peer("retired-reader")
        self.forum.retire_agent(retired["id"], "Finished")
        # Simulate the pre-attention schema while keeping the legacy cursors.
        with self.forum._connection() as connection:
            connection.execute("DROP TABLE notification_deliveries")
            connection.execute("DROP TABLE thread_subscriptions")
            connection.execute("DROP TABLE forum_migrations")
        with ThreadPoolExecutor(max_workers=2) as executor:
            reopened = list(executor.map(lambda _: Forum(self.forum.state_dir), range(2)))
        migrated = reopened[0]
        self.assertEqual([expected], [item["id"] for item in migrated.pending_notifications(peer["id"])])
        self.assertEqual([], migrated.pending_notifications(late["id"]))
        self.assertEqual([], migrated.pending_notifications(retired["id"]))
        migrated.acknowledge_notifications(peer["id"], [expected])
        reopened_again = Forum(self.forum.state_dir)
        self.assertEqual([], reopened_again.pending_notifications(peer["id"]))

    def test_cross_run_subscriptions_and_invalid_page_arguments_are_rejected(self) -> None:
        peer = self.peer("reader")
        other = self.forum.create_run("Another run", self.root)
        topic = self.forum.create_thread(other["id"], "human", "Other topic", "Evidence")
        with self.assertRaises(ValueError):
            self.forum.subscribe(peer["id"], topic["id"])
        with self.assertRaises(ValueError):
            self.forum.unsubscribe(peer["id"], topic["id"])
        for limit in (0, 101, True):
            with self.assertRaises(ValueError):
                self.forum.activity_page(peer["id"], limit=limit)
        with self.assertRaises(ValueError):
            self.forum.activity_page(peer["id"], scope="private")
        with self.assertRaises(ValueError):
            self.forum.activity_page(peer["id"], after=-1)

    def test_directory_subscriptions_and_comments_have_stable_pages(self) -> None:
        peers = [self.peer(f"reader-{number}") for number in range(7)]
        first = self.forum.search_agents(self.run["id"], query="reader-", limit=3)
        second = self.forum.search_agents(self.run["id"], after=first["next_cursor"], limit=10)
        self.assertEqual(7, len({item["id"] for item in first["items"] + second["items"]}))
        self.assertNotIn("session_id", first["items"][0])
        self.assertNotIn("pid", first["items"][0])
        self.assertEqual([], self.forum.search_agents(self.run["id"], query="%_")["items"])
        topics = [self.thread() for _ in range(4)]
        for topic in topics:
            self.forum.subscribe(peers[0]["id"], topic["id"])
        following = self.forum.list_subscriptions(peers[0]["id"], limit=2)
        rest = self.forum.list_subscriptions(peers[0]["id"], after=following["next_cursor"])
        self.assertEqual(4, len({item["thread_id"] for item in following["items"] + rest["items"]}))

        topic = topics[0]
        comments = [self.forum.add_comment(topic["id"], "writer", str(number)) for number in range(5)]
        with self.forum._connection() as connection:
            connection.execute("UPDATE comments SET created_at = 'same-time' WHERE thread_id = ?", (topic["id"],))
        page = self.forum.comments_page(topic["id"], limit=2)
        rest = self.forum.comments_page(topic["id"], after=page["next_cursor"])
        self.assertEqual({item["id"] for item in comments}, {item["id"] for item in page["items"] + rest["items"]})
        header = self.forum.get_thread(topic["id"], include_comments=False)
        self.assertEqual([], header["comments"])
        self.assertEqual(5, header["comment_count"])
        self.assertEqual(comments[0]["body"], self.forum.get_comment(topic["id"], comments[0]["id"])["body"])
        with self.assertRaises(KeyError):
            self.forum.get_comment(topics[1]["id"], comments[0]["id"])


if __name__ == "__main__":
    unittest.main()
