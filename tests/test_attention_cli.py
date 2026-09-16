from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from idea import cli
from idea.domain import AgentProfile, Effort, ProcessState, Provider
from idea.execution import RunLock
from idea.forum import Forum
from idea.launcher import prepare_run


class AttentionCliTest(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.workspace = Path(self.directory.name).resolve()
        self.forum = Forum(self.workspace / ".idea")
        names = ("reader", "writer-a", "writer-b", "literal_%", "plain")
        self.prepared = prepare_run(
            forum=self.forum, goal="Improve a shared document", workspace=self.workspace,
            profiles=tuple(AgentProfile(name, Provider.OPENAI, "fake-model", Effort.LOW) for name in names),
        )
        self.run_id = str(self.prepared.run["id"])
        self.agent_id = str(self.prepared.peers[0].agent["id"])
        self.environment = {
            "IDEA_STATE_DIR": str(self.forum.state_dir), "IDEA_RUN_ID": self.run_id,
            "IDEA_AGENT_ID": self.agent_id, "IDEA_AGENT_NAME": "reader",
        }

    def forum_command(self, *arguments: str):
        output = io.StringIO()
        with patch.dict(os.environ, self.environment), redirect_stdout(output):
            code = cli.handle_forum([
                "--state-dir", str(self.forum.state_dir), "--run", self.run_id,
                *arguments, "--json",
            ])
        self.assertEqual(0, code)
        return json.loads(output.getvalue())

    def test_follow_unfollow_and_following_pages_preserve_wake_choice(self) -> None:
        threads = [self.forum.create_thread(self.run_id, "writer-a", f"Topic {index}", "Initial context") for index in range(3)]
        for thread in threads:
            value = self.forum_command("follow", str(thread["id"]))
            self.assertFalse(value["wake"])
        self.forum.add_comment(str(threads[1]["id"]), "writer-a", "Digest-only update")
        self.assertEqual([], self.forum.pending_notifications(self.agent_id))
        value = self.forum_command("follow", str(threads[0]["id"]), "--wake")
        self.assertTrue(value["wake"])
        self.forum.add_comment(str(threads[0]["id"]), "writer-a", "Wake-enabled update")
        self.assertEqual("subscription", self.forum.pending_notifications(self.agent_id)[0]["notification_reason"])
        collected = []
        cursor = None
        for _ in range(4):
            arguments = ["following", "--limit", "1"]
            if cursor:
                arguments += ["--after", cursor]
            page = self.forum_command(*arguments)
            self.assertLessEqual(len(page["items"]), 1)
            collected.extend(page["items"])
            cursor = page["next_cursor"]
            if cursor is None:
                break
        self.assertEqual({str(item["id"]) for item in threads}, {item["thread_id"] for item in collected})
        self.assertEqual(3, len(collected))
        self.assertEqual(1, sum(item["wake"] for item in collected))
        removed = self.forum_command("unfollow", str(threads[0]["id"]))
        self.assertTrue(removed["removed"])
        remaining = self.forum_command("following")
        self.assertNotIn(str(threads[0]["id"]), {item["thread_id"] for item in remaining["items"]})

    def test_inbox_advances_only_consumed_page_and_does_not_acknowledge_delivery(self) -> None:
        thread = self.forum.create_thread(self.run_id, "writer-a", "Followed", "Initial context")
        self.forum_command("follow", str(thread["id"]))
        for index in range(4):
            self.forum.add_comment(str(thread["id"]), "writer-a", f"Digest item {index}")
        self.forum.create_thread(self.run_id, "user", "Direct request", "Please inspect @reader")
        pending_before = [int(item["id"]) for item in self.forum.pending_notifications(self.agent_id)]
        expected = self.forum.activity_page(self.agent_id, scope="following", after=0, limit=100)["items"]
        first = self.forum_command("inbox", "--scope", "following", "--limit", "2")
        self.assertTrue(first["has_more"])
        self.assertEqual([item["id"] for item in expected[:2]], [item["id"] for item in first["items"]])
        self.assertEqual(first["items"][-1]["id"], first["through_id"])
        self.assertEqual(first["through_id"], self.forum.get_agent(self.agent_id)["last_activity_id"])
        self.assertLess(first["through_id"], pending_before[-1])
        second = self.forum_command("inbox", "--limit", "2", "--after", str(first["next_cursor"]))
        self.assertEqual([item["id"] for item in expected[2:4]], [item["id"] for item in second["items"]])
        self.assertEqual(second["items"][-1]["id"], self.forum.get_agent(self.agent_id)["last_activity_id"])
        third = self.forum_command("inbox", "--limit", "2", "--after", str(second["next_cursor"]))
        self.assertFalse(third["has_more"])
        self.assertEqual([item["id"] for item in expected[4:]], [item["id"] for item in third["items"]])
        self.assertEqual(pending_before, [int(item["id"]) for item in self.forum.pending_notifications(self.agent_id)])

    def test_discovery_pages_public_activity_without_consuming_inbox(self) -> None:
        for index in range(4):
            self.forum.create_thread(self.run_id, "writer-a", f"Public topic {index}", "Unsubscribed discussion")
        self.forum.create_thread(self.run_id, "user", "Pending mention", "Please inspect @reader")
        before_cursor = self.forum.get_agent(self.agent_id)["last_activity_id"]
        before_pending = [item["id"] for item in self.forum.pending_notifications(self.agent_id)]
        expected = self.forum.activity_page(self.agent_id, scope="all", after=0, limit=100)["items"]
        collected = []
        cursor = 0
        for _ in range(10):
            page = self.forum_command("discover", "--limit", "2", "--after", str(cursor))
            collected.extend(page["items"])
            self.assertLessEqual(len(page["items"]), 2)
            cursor = page["next_cursor"]
            if cursor is None:
                break
        self.assertEqual([item["id"] for item in expected], [item["id"] for item in collected])
        self.assertEqual(before_cursor, self.forum.get_agent(self.agent_id)["last_activity_id"])
        self.assertEqual(before_pending, [item["id"] for item in self.forum.pending_notifications(self.agent_id)])

    def test_peer_directory_search_and_pagination_do_not_repeat_peers(self) -> None:
        matched = self.forum_command("peers", "--query", "literal_%", "--limit", "2")
        self.assertEqual(["literal_%"], [item["name"] for item in matched["items"]])
        collected = []
        cursor = None
        for _ in range(4):
            arguments = ["peers", "--limit", "2"]
            if cursor:
                arguments += ["--after", cursor]
            page = self.forum_command(*arguments)
            self.assertLessEqual(len(page["items"]), 2)
            collected.extend(page["items"])
            cursor = page["next_cursor"]
            if cursor is None:
                break
        self.assertEqual(5, len(collected))
        self.assertEqual({peer.profile.name for peer in self.prepared.peers}, {item["name"] for item in collected})
        self.assertEqual(5, len({item["id"] for item in collected}))

    def test_read_paginates_comments_while_preserving_thread_body(self) -> None:
        body = "The complete reference text\n" * 100
        thread = self.forum.create_thread(self.run_id, "writer-a", "Reference", body)
        comments = [self.forum.add_comment(str(thread["id"]), "writer-a", f"Reply {index}") for index in range(3)]
        first = self.forum_command("read", str(thread["id"]), "--limit", "2")
        self.assertEqual(body, first["body"])
        self.assertEqual([item["id"] for item in comments[:2]], [item["id"] for item in first["comments"]])
        second = self.forum_command("read", str(thread["id"]), "--limit", "2", "--after", first["next_cursor"])
        self.assertEqual(body, second["body"])
        self.assertEqual([comments[2]["id"]], [item["id"] for item in second["comments"]])
        self.assertIsNone(second["next_cursor"])

    def test_execution_cap_validation_precedes_any_run_preparation(self) -> None:
        for option in ("--max-concurrent", "--max-codex", "--max-claude"):
            for value in ("0", "501"):
                with self.subTest(option=option, value=value):
                    errors = io.StringIO()
                    with patch.object(cli, "prepare_run") as prepare, redirect_stderr(errors):
                        code = cli.main(["run", "goal", "--dry-run", "--workspace", str(self.workspace), option, value])
                    self.assertEqual(2, code)
                    self.assertIn("between 1 and 500", errors.getvalue())
                    prepare.assert_not_called()
        output = io.StringIO()
        with patch.object(cli, "_execute_prepared", return_value=0) as execute, redirect_stdout(output):
            code = cli.main([
                "run", "goal", "--workspace", str(self.workspace), "--state-dir", str(self.forum.state_dir),
                "--agent", "openai:fake-model:low", "--no-web", "--max-concurrent", "7",
                "--max-codex", "3", "--max-claude", "2",
            ])
        self.assertEqual(0, code)
        self.assertEqual((7, 3, 2), tuple(execute.call_args.kwargs[key] for key in ("max_concurrent", "max_codex", "max_claude")))

    def test_resume_keeps_one_lock_from_preparation_through_execution(self) -> None:
        for peer in self.prepared.peers:
            self.forum.set_process_state(str(peer.agent["id"]), ProcessState.DORMANT, exit_code=0)
        held_descriptors = []

        def execute_while_locked(**kwargs):
            held = kwargs["run_lock"]
            held_descriptors.append(held.fd)
            self.assertIsNotNone(held.fd)
            os.fstat(held.fd)
            self.assertIs(prepare.call_args.kwargs["run_lock"], held)
            with self.assertRaisesRegex(RuntimeError, "already has a launcher"):
                with RunLock(self.forum.state_dir, self.run_id):
                    self.fail("Resume released its lock before execution")
            return 0

        output = io.StringIO()
        with patch.object(cli, "prepare_resume", wraps=cli.prepare_resume) as prepare:
            with patch.object(cli, "_execute_prepared", side_effect=execute_while_locked), redirect_stdout(output):
                code = cli.main(["resume", self.run_id, "--state-dir", str(self.forum.state_dir), "--no-web"])
        self.assertEqual(0, code)
        self.assertEqual(1, len(held_descriptors))
        with RunLock(self.forum.state_dir, self.run_id):
            pass


if __name__ == "__main__":
    unittest.main()
