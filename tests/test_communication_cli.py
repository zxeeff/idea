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
from idea.communication import CommunicationPolicy, CommunicationStore
from idea.domain import AgentProfile, Effort, Provider
from idea.forum import Forum


class CommunicationCliTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.workspace = Path(self.temp.name).resolve()
        self.state_dir = self.workspace / ".idea"

    def preview(self, *args):
        with redirect_stdout(io.StringIO()):
            result = cli.main([
                "run", "Review evidence", "--workspace", str(self.workspace),
                "--state-dir", str(self.state_dir), "--dry-run",
                "--agent", "openai:test:low", *args,
            ])
        self.assertEqual(0, result)
        forum = Forum(self.state_dir)
        return forum, str(forum.list_runs()[0]["id"])

    def command(self, run_id, *args):
        output = io.StringIO()
        with redirect_stdout(output):
            result = cli.main(["forum", "--state-dir", str(self.state_dir), "--run", run_id, *args, "--json"])
        self.assertEqual(0, result)
        return json.loads(output.getvalue())

    def test_notification_policy_defaults_persist_without_starting_models(self):
        forum, run_id = self.preview()
        self.assertEqual(CommunicationPolicy(1, 5), CommunicationStore(forum, run_id).policy())
        self.assertEqual([], forum.pending_notifications(forum.list_agents(run_id)[0]["id"]))

    def test_resume_preserves_saved_values_and_only_changes_explicit_options(self):
        forum, run_id = self.preview("--notification-debounce", "2.5", "--notification-max-wait", "12")
        store = CommunicationStore(forum, run_id)
        base = ["resume", run_id, "--state-dir", str(self.state_dir), "--dry-run"]
        with redirect_stdout(io.StringIO()):
            self.assertEqual(0, cli.main(base))
        self.assertEqual(CommunicationPolicy(2.5, 12), store.policy())
        with redirect_stdout(io.StringIO()):
            self.assertEqual(0, cli.main(base + ["--notification-debounce", "8"]))
        self.assertEqual(CommunicationPolicy(8, 12), store.policy())
        with redirect_stderr(io.StringIO()), patch.object(cli, "prepare_resume") as prepare:
            self.assertEqual(2, cli.main(base + ["--notification-max-wait", "4"]))
        prepare.assert_not_called()
        self.assertEqual(CommunicationPolicy(8, 12), store.policy())
        with redirect_stdout(io.StringIO()):
            self.assertEqual(0, cli.main(base + ["--notification-debounce", "0", "--notification-max-wait", "0"]))
        self.assertEqual(CommunicationPolicy(0, 0), store.policy())

    def test_invalid_notification_options_do_not_create_a_run(self):
        for args in (
            ["--notification-debounce", "-1"], ["--notification-debounce", "nan"],
            ["--notification-max-wait", "inf"], ["--notification-max-wait", "-1"],
            ["--notification-debounce", "6"], ["--notification-max-wait", "0"],
        ):
            with self.subTest(args=args), redirect_stderr(io.StringIO()), patch.object(cli, "prepare_run") as prepare:
                self.assertEqual(2, cli.main(["run", "goal", "--workspace", str(self.workspace), "--state-dir", str(self.state_dir), "--dry-run", *args]))
            prepare.assert_not_called()
        self.assertFalse(self.state_dir.exists())

    def test_reply_records_target_validation_and_evidence(self):
        forum, run_id = self.preview()
        thread = forum.create_thread(run_id, "human", "Claim", "Original")
        evidence = forum.add_comment(thread["id"], "human", "Observed result")
        reply = self.command(run_id, "reply", thread["id"], "--body", "Author's report", "--reply-to", str(thread["event_id"]),
                             "--relation", "verifies", "--validation", "3 checks passed; limited fixture", "--evidence-event", str(evidence["event_id"]))
        metadata = reply["provenance"]
        self.assertEqual("verifies", metadata["relation"])
        self.assertEqual(thread["event_id"], metadata["reply_to_event_id"])
        self.assertEqual([evidence["event_id"]], metadata["evidence_event_ids"])
        self.assertEqual("3 checks passed; limited fixture", metadata["validation"])

    def test_reply_trigger_keeps_original_event_and_rejects_conflicting_target(self):
        forum, run_id = self.preview()
        peer = forum.register_agent(run_id, AgentProfile("reader", Provider.OPENAI, "test", Effort.LOW))
        thread = forum.create_thread(run_id, "human", "Request", "Inspect this @reader")
        unrelated = forum.create_thread(run_id, "human", "Other", "Other event")
        environment = {"IDEA_AGENT_ID": peer["id"], "IDEA_AGENT_NAME": "reader", "IDEA_TRIGGER_EVENT_ID": str(thread["event_id"]), "IDEA_TRIGGER_THREAD_ID": thread["id"]}
        with patch.dict(os.environ, environment):
            reply = self.command(run_id, "reply-trigger", "--body", "Response")
            self.assertEqual(thread["event_id"], reply["provenance"]["reply_to_event_id"])
            with redirect_stderr(io.StringIO()):
                result = cli.main(["forum", "--state-dir", str(self.state_dir), "--run", run_id, "reply-trigger", "--body", "Wrong target", "--reply-to", str(unrelated["event_id"])])
            self.assertEqual(2, result)
        self.assertEqual(1, len(forum.get_thread(thread["id"])["comments"]))

    def test_changes_pages_preserve_frozen_upper_bound_during_new_activity(self):
        forum, run_id = self.preview()
        thread = forum.create_thread(run_id, "human", "Discussion", "Original")
        for number in range(4):
            forum.add_comment(thread["id"], "human", f"Initial {number}")
        page = self.command(run_id, "changes", thread["id"], "--limit", "2")
        frozen = page["through_event"]
        collected = list(page["items"])
        late = forum.add_comment(thread["id"], "human", "Arrived after first page")
        while page["next_cursor"] is not None:
            page = self.command(run_id, "changes", thread["id"], "--limit", "2", "--after-event", str(page["next_cursor"]), "--through-event", str(frozen))
            self.assertEqual(frozen, page["through_event"])
            collected.extend(page["items"])
        self.assertEqual(5, len(collected))
        self.assertNotIn(late["event_id"], [item["event_id"] for item in collected])
        self.assertEqual(5, len({item["event_id"] for item in collected}))


if __name__ == "__main__":
    unittest.main()
