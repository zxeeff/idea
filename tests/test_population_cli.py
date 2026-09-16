from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from idea import cli
from idea.forum import Forum
from idea.population import PopulationStore
from idea.workspaces import WorkspaceStore


class PopulationCliTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.workspace = Path(self.directory.name).resolve()
        (self.workspace / "sample.txt").write_text("initial\n")
        self.state_dir = self.workspace / ".idea-swarm"

    def run_preview(self, *arguments):
        with redirect_stdout(io.StringIO()):
            code = cli.main(["run", "Improve the sample", "--workspace", str(self.workspace),
                             "--state-dir", str(self.state_dir), "--dry-run", *arguments])
        self.assertEqual(0, code)
        forum = Forum(self.state_dir)
        run = forum.list_runs()[0]
        return forum, str(run["id"])

    def test_new_run_uses_adaptive_templates_and_independent_working_copies(self):
        forum, run_id = self.run_preview("--agent", "openai:fake-model:low", "--initial-agents", "2",
                                         "--max-agents", "4", "--max-invocations", "7")
        population = PopulationStore(forum, run_id)
        self.assertTrue(population.enabled)
        self.assertEqual(1, len(population.templates()))
        self.assertEqual(2, len(forum.list_agents(run_id)))
        self.assertEqual(0, population.summary()["invocations_started"])
        self.assertEqual(2, population.summary()["total_births"])
        copies = WorkspaceStore(forum, run_id)
        self.assertEqual("isolated", copies.summary()["mode"])
        self.assertEqual(2, copies.summary()["prepared_count"])
        for agent in forum.list_agents(run_id):
            self.assertNotEqual(str(self.workspace), copies.get(agent["id"])["path"])

    def test_fixed_mode_preserves_requested_count_and_shared_compatibility(self):
        forum, run_id = self.run_preview("--population", "fixed", "--workspace-mode", "shared",
                                         "--agent", "openai:fake-model:low:2")
        population = PopulationStore(forum, run_id)
        self.assertFalse(population.enabled)
        self.assertEqual(2, population.policy().initial_agents)
        self.assertEqual(2, len(forum.list_agents(run_id)))
        self.assertEqual(0, population.summary()["total_births"])
        copies = WorkspaceStore(forum, run_id)
        for agent in forum.list_agents(run_id):
            self.assertEqual(str(self.workspace), copies.get(agent["id"])["path"])

    def test_invalid_policy_fails_before_preparing_any_run(self):
        for arguments in (
            ("--max-agents", "501"), ("--initial-agents", "0"), ("--birth-burst", "0"),
            ("--births-per-minute", "nan"), ("--max-births", "0"), ("--max-invocations", "0"),
            ("--participation-grace", "1800"),
            ("--max-open-calls-per-agent", "0"), ("--max-open-calls-per-agent", "501"),
            ("--max-offers-per-call", "-1"), ("--max-offers-per-call", "501"),
            ("--offer-cooldown", "-1"), ("--offer-cooldown", "nan"),
            ("--idle-timeout", "0"), ("--idle-timeout", "inf"),
        ):
            with self.subTest(arguments=arguments), patch.object(cli, "prepare_run") as prepare, redirect_stderr(io.StringIO()):
                code = cli.main(["run", "goal", "--workspace", str(self.workspace), "--dry-run", *arguments])
            self.assertEqual(2, code)
            prepare.assert_not_called()
        self.assertFalse(self.state_dir.exists())

    def test_participation_options_persist_and_resume_changes_only_explicit_values(self):
        forum, run_id = self.run_preview(
            "--agent", "openai:fake-model:low", "--initial-agents", "1",
            "--max-open-calls-per-agent", "7", "--max-offers-per-call", "3",
            "--offer-cooldown", "45.5", "--idle-timeout", "90",
        )
        before = PopulationStore(forum, run_id).policy()
        self.assertEqual((7, 3, 45.5, 90), (
            before.max_open_calls_per_agent, before.max_offers_per_call,
            before.offer_cooldown, before.idle_timeout,
        ))
        with redirect_stdout(io.StringIO()):
            code = cli.main(["resume", run_id, "--state-dir", str(self.state_dir), "--dry-run"])
        self.assertEqual(0, code)
        self.assertEqual(before, PopulationStore(forum, run_id).policy())

        with redirect_stdout(io.StringIO()):
            code = cli.main([
                "resume", run_id, "--state-dir", str(self.state_dir), "--dry-run",
                "--max-offers-per-call", "0", "--offer-cooldown", "0", "--idle-timeout", "120",
            ])
        self.assertEqual(0, code)
        after = PopulationStore(forum, run_id).policy()
        self.assertEqual((7, 0, 0, 120), (
            after.max_open_calls_per_agent, after.max_offers_per_call,
            after.offer_cooldown, after.idle_timeout,
        ))
        self.assertEqual(before.max_agents, after.max_agents)
        self.assertEqual(before.max_invocations, after.max_invocations)

        with redirect_stderr(io.StringIO()):
            code = cli.main([
                "resume", run_id, "--state-dir", str(self.state_dir), "--dry-run",
                "--idle-timeout", "0",
            ])
        self.assertEqual(2, code)
        self.assertEqual(after, PopulationStore(forum, run_id).policy())

    def test_new_participation_defaults_and_failed_call_filter(self):
        forum, run_id = self.run_preview("--agent", "openai:fake-model:low", "--initial-agents", "1")
        policy = PopulationStore(forum, run_id).policy()
        self.assertEqual((4, 2, 300, 300), (
            policy.max_open_calls_per_agent, policy.max_offers_per_call,
            policy.offer_cooldown, policy.idle_timeout,
        ))
        output = io.StringIO()
        with redirect_stdout(output):
            code = cli.main([
                "forum", "--state-dir", str(self.state_dir), "--run", run_id,
                "calls", "--state", "failed", "--json",
            ])
        self.assertEqual(0, code)
        self.assertIn('"items": []', output.getvalue())


if __name__ == "__main__":
    unittest.main()
