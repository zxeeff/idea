from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from idea.cli import handle_forum
from idea.domain import AgentProfile, Effort, ProcessState, Provider
from idea.forum import Forum


class ForumTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.forum = Forum(self.root / ".idea")
        self.run = self.forum.create_run("fix the failing test", self.root)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_free_form_thread_reply_and_attachment(self) -> None:
        profile = AgentProfile("peer-a", Provider.OPENAI, "gpt-5.6-sol", Effort.XHIGH)
        agent = self.forum.register_agent(self.run["id"], profile)
        self.forum.set_process_state(agent["id"], ProcessState.RUNNING, pid=42)
        thread = self.forum.create_thread(
            self.run["id"], "peer-a", "arbitrary title", "arbitrary body"
        )
        comment = self.forum.add_comment(thread["id"], "peer-b", "an untyped reply")
        sample = self.root / "proof.bin"
        sample.write_bytes(b"proof")
        attachment = self.forum.add_attachment(
            self.run["id"], "peer-b", sample, thread_id=thread["id"], description="try this"
        )

        loaded = self.forum.get_thread(thread["id"])
        self.assertEqual(comment["id"], loaded["comments"][0]["id"])
        self.assertEqual(attachment["sha256"], loaded["attachments"][0]["sha256"])
        self.assertEqual([thread["id"]], [item["id"] for item in self.forum.search(self.run["id"], "untyped")])
        self.assertEqual("running", self.forum.get_agent(agent["id"])["process_state"])

    def test_concurrent_peers_can_post_without_a_central_writer(self) -> None:
        def post(number: int) -> str:
            return self.forum.create_thread(
                self.run["id"], f"peer-{number}", f"thread {number}", "body"
            )["id"]

        with ThreadPoolExecutor(max_workers=8) as pool:
            ids = list(pool.map(post, range(32)))
        self.assertEqual(32, len(set(ids)))
        self.assertEqual(32, len(self.forum.list_threads(self.run["id"])))

    def test_activity_cursors_mentions_and_retirement_are_persisted(self) -> None:
        peer_a = self.forum.register_agent(
            self.run["id"],
            AgentProfile("peer-a", Provider.OPENAI, "gpt-5.6-luna", Effort.LOW),
        )
        peer_b = self.forum.register_agent(
            self.run["id"],
            AgentProfile("peer-b", Provider.ANTHROPIC, "sonnet", Effort.HIGH),
        )
        general = self.forum.create_thread(
            self.run["id"], "human", "general", "new evidence"
        )
        wake_a, _ = self.forum.wake_signal(peer_a["id"])
        wake_b, _ = self.forum.wake_signal(peer_b["id"])
        self.assertFalse(wake_a)
        self.assertFalse(wake_b)
        activity_a, high_water = self.forum.unseen_activity(peer_a["id"])
        activity_b, _ = self.forum.unseen_activity(peer_b["id"])
        self.assertEqual([general["id"]], [item["subject_id"] for item in activity_a])
        self.assertEqual([general["id"]], [item["subject_id"] for item in activity_b])
        self.forum.mark_activity_seen(peer_a["id"], high_water)
        self.forum.mark_activity_seen(peer_b["id"], high_water)

        targeted = self.forum.create_thread(
            self.run["id"], "human", "for one peer", "please inspect this @peer-a"
        )
        activity_a, targeted_high_water = self.forum.unseen_activity(peer_a["id"])
        activity_b, peer_b_high_water = self.forum.unseen_activity(peer_b["id"])
        wake_a, _ = self.forum.wake_signal(peer_a["id"])
        wake_b, _ = self.forum.wake_signal(peer_b["id"])
        self.assertEqual([targeted["id"]], [item["subject_id"] for item in activity_a])
        self.assertEqual([], activity_b)
        self.assertTrue(wake_a)
        self.assertFalse(wake_b)
        self.assertEqual(targeted_high_water, peer_b_high_water)

        retired = self.forum.retire_agent(peer_a["id"], "objective is documented")
        self.assertEqual("retired", retired["process_state"])
        self.assertEqual("objective is documented", retired["retire_reason"])

    def test_mentions_match_complete_agent_names_without_prefix_collisions(self) -> None:
        original = self.forum.register_agent(
            self.run["id"],
            AgentProfile("opus-max", Provider.ANTHROPIC, "opus", Effort.MAX),
        )
        duplicate = self.forum.register_agent(
            self.run["id"],
            AgentProfile("opus-max-2", Provider.ANTHROPIC, "opus", Effort.MAX),
        )

        targeted = self.forum.create_thread(
            self.run["id"], "human", "exact target", "please inspect @opus-max-2."
        )
        original_activity, _ = self.forum.unseen_activity(original["id"])
        duplicate_activity, _ = self.forum.unseen_activity(duplicate["id"])
        original_wake, _ = self.forum.wake_signal(original["id"])
        duplicate_wake, high_water = self.forum.wake_signal(duplicate["id"])

        self.assertEqual([], original_activity)
        self.assertEqual([targeted["id"]], [item["subject_id"] for item in duplicate_activity])
        self.assertFalse(original_wake)
        self.assertTrue(duplicate_wake)

        self.forum.mark_activity_seen(original["id"], high_water)
        self.forum.mark_activity_seen(duplicate["id"], high_water)
        passive = self.forum.create_thread(
            self.run["id"], "human", "not a known mention", "look at @opus-max-20"
        )
        original_activity, _ = self.forum.unseen_activity(original["id"])
        duplicate_activity, _ = self.forum.unseen_activity(duplicate["id"])
        original_wake, _ = self.forum.wake_signal(original["id"])
        duplicate_wake, _ = self.forum.wake_signal(duplicate["id"])
        self.assertEqual([passive["id"]], [item["subject_id"] for item in original_activity])
        self.assertEqual([passive["id"]], [item["subject_id"] for item in duplicate_activity])
        self.assertFalse(original_wake)
        self.assertFalse(duplicate_wake)

        mentioned = self.forum.create_thread(
            self.run["id"], "human", "wake original", "please inspect @opus-max"
        )
        original_activity, mentioned_high_water = self.forum.unseen_activity(original["id"])
        duplicate_activity, _ = self.forum.unseen_activity(duplicate["id"])
        original_wake, _ = self.forum.wake_signal(original["id"])
        duplicate_wake, _ = self.forum.wake_signal(duplicate["id"])
        self.assertEqual(
            [passive["id"], mentioned["id"]],
            [item["subject_id"] for item in original_activity],
        )
        self.assertEqual([passive["id"]], [item["subject_id"] for item in duplicate_activity])
        self.assertTrue(original_wake)
        self.assertFalse(duplicate_wake)

        self.forum.mark_activity_seen(original["id"], mentioned_high_water)
        self.forum.mark_activity_seen(duplicate["id"], mentioned_high_water)
        broadcast = self.forum.create_thread(
            self.run["id"], "human", "everyone", "please compare @all"
        )
        original_wake, _ = self.forum.wake_signal(original["id"])
        duplicate_wake, _ = self.forum.wake_signal(duplicate["id"])
        self.assertTrue(original_wake)
        self.assertTrue(duplicate_wake)
        self.assertEqual(
            [broadcast["id"]],
            [item["subject_id"] for item in self.forum.unseen_activity(original["id"])[0]],
        )

    def test_wake_events_are_enriched_and_separate_from_passive_background(self) -> None:
        peer = self.forum.register_agent(
            self.run["id"],
            AgentProfile("peer-a", Provider.OPENAI, "gpt-5.6-sol", Effort.HIGH),
        )
        passive = self.forum.create_thread(
            self.run["id"], "human", "background result", "useful but not urgent"
        )
        target = self.forum.create_thread(
            self.run["id"],
            "human",
            "direct question",
            "please compare this path @peer-a",
        )

        triggers, high_water = self.forum.wake_events(peer["id"])
        inbox, _ = self.forum.unseen_activity(peer["id"])

        self.assertEqual([target["id"]], [item["subject_id"] for item in triggers])
        self.assertEqual("direct question", triggers[0]["thread_title"])
        self.assertEqual("please compare this path @peer-a", triggers[0]["content"])
        self.assertEqual(
            [passive["id"], target["id"]], [item["subject_id"] for item in inbox]
        )
        self.assertEqual(target["id"], self.forum.resolve_reply_trigger(peer["id"])["thread_id"])
        self.assertEqual(
            target["id"],
            self.forum.resolve_reply_trigger(peer["id"], triggers[0]["id"])["thread_id"],
        )

        self.forum.mark_wake_scanned(peer["id"], high_water)
        self.assertEqual([], self.forum.wake_events(peer["id"])[0])

    def test_reply_trigger_cli_routes_to_the_mention_thread(self) -> None:
        peer = self.forum.register_agent(
            self.run["id"],
            AgentProfile("peer-a", Provider.OPENAI, "gpt-5.6-sol", Effort.HIGH),
        )
        unrelated = self.forum.create_thread(
            self.run["id"], "peer-b", "old lane", "ongoing research"
        )
        target = self.forum.create_thread(
            self.run["id"], "human", "answer here", "question for @peer-a"
        )
        trigger = self.forum.wake_events(peer["id"])[0][0]
        environment = {
            "IDEA_STATE_DIR": str(self.forum.state_dir),
            "IDEA_RUN_ID": self.run["id"],
            "IDEA_AGENT_ID": peer["id"],
            "IDEA_AGENT_NAME": "peer-a",
            "IDEA_TRIGGER_EVENT_ID": str(trigger["id"]),
            "IDEA_TRIGGER_THREAD_ID": target["id"],
        }
        output = io.StringIO()
        with patch.dict(os.environ, environment, clear=False), redirect_stdout(output):
            result = handle_forum(
                ["reply-trigger", "--body", "direct answer", "--json"]
            )

        payload = json.loads(output.getvalue())
        self.assertEqual(0, result)
        self.assertEqual(trigger["id"], payload["trigger_event_id"])
        self.assertEqual(target["id"], payload["trigger_thread_id"])
        self.assertEqual([], self.forum.get_thread(unrelated["id"])["comments"])
        comments = self.forum.get_thread(target["id"])["comments"]
        self.assertEqual(["direct answer"], [item["body"] for item in comments])

        newer = self.forum.create_thread(
            self.run["id"], "human", "newer mention", "another question @peer-a"
        )
        newer_trigger = self.forum.wake_events(peer["id"])[0][-1]
        conflicting_environment = environment | {
            "IDEA_TRIGGER_EVENT_ID": str(newer_trigger["id"]),
            "IDEA_TRIGGER_THREAD_ID": newer["id"],
        }
        with patch.dict(os.environ, conflicting_environment, clear=False), redirect_stdout(
            io.StringIO()
        ):
            handle_forum(
                [
                    "reply-trigger",
                    "--event",
                    str(trigger["id"]),
                    "--body",
                    "explicit older answer",
                    "--json",
                ]
            )
        self.assertEqual([], self.forum.get_thread(newer["id"])["comments"])
        self.assertEqual(
            ["direct answer", "explicit older answer"],
            [item["body"] for item in self.forum.get_thread(target["id"])["comments"]],
        )

    def test_human_mentions_are_exact_bounded_web_events(self) -> None:
        thread = self.forum.create_thread(
            self.run["id"], "peer-a", "research lane", "initial result"
        )
        baseline = self.forum.activity_high_water(self.run["id"])
        mentioned = self.forum.add_comment(
            thread["id"], "peer-a", "@human please choose between these paths"
        )
        self.forum.add_comment(thread["id"], "human", "self-note with @human")
        self.forum.add_comment(thread["id"], "peer-b", "not the same @human-2")

        result = self.forum.human_mentions(self.run["id"], baseline)

        self.assertFalse(result["has_more"])
        self.assertEqual(self.forum.activity_high_water(self.run["id"]), result["cursor"])
        self.assertEqual([mentioned["id"]], [item["subject_id"] for item in result["items"]])
        self.assertEqual(thread["id"], result["items"][0]["thread_id"])
        self.assertEqual("research lane", result["items"][0]["thread_title"])
        self.assertEqual("@human", result["items"][0]["mention"])
        self.assertIn("please choose", result["items"][0]["preview"])


if __name__ == "__main__":
    unittest.main()
