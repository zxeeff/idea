from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

from idea.domain import AgentProfile, Effort, ProcessState, Provider
from idea.forum import Forum
from idea.launcher import prepare_run, prepare_resume, run_reactor
from idea.population import PopulationPolicy, PopulationStore


class PopulationRuntimeTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.workspace = Path(self.directory.name).resolve()
        (self.workspace / "example.txt").write_text("baseline\n")
        self.forum = Forum(self.workspace / ".idea-swarm")
        self.profiles = (
            AgentProfile("codex-template", Provider.OPENAI, "fake-codex", Effort.LOW),
            AgentProfile("claude-template", Provider.ANTHROPIC, "fake-claude", Effort.LOW),
        )

    async def command(self, invocation, *arguments):
        process = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "idea", "forum", *arguments, "--json",
            cwd=invocation.cwd, env=invocation.env,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(), 10)
        self.assertEqual(0, process.returncode, stderr.decode())
        return json.loads(stdout)

    def test_public_recruitment_joins_in_original_project_folder(self):
        policy = PopulationPolicy(initial_agents=2, max_agents=4, birth_burst=4,
                                  participation_grace=0, max_births=4, max_invocations=8)
        prepared = prepare_run(forum=self.forum, goal="Improve the example independently", workspace=self.workspace,
                               profiles=self.profiles, population_policy=policy, adaptive=True)
        run_id = str(prepared.run["id"])
        thread = self.forum.create_thread(run_id, "human", "An independent approach", "Explore another approach")
        initial = {str(peer.agent["id"]) for peer in prepared.peers}
        creator = str(prepared.peers[0].agent["id"])
        calls, paths = [], []

        async def runner(**kwargs):
            agent, invocation = kwargs["agent"], kwargs["invocation"]
            calls.append(str(agent["id"]))
            paths.append(invocation.cwd)
            self.assertEqual(self.workspace, invocation.cwd)
            self.assertEqual(str(self.forum.state_dir), invocation.env["IDEA_STATE_DIR"])
            self.assertNotIn("IDEA_BRIDGE_DIR", invocation.env)
            self.assertEqual("baseline\n", (invocation.cwd / "example.txt").read_text())
            self.forum.set_process_state(str(agent["id"]), ProcessState.RUNNING, session_id="fake-" + str(agent["id"]))
            if str(agent["id"]) == creator:
                invitation = await self.command(invocation, "recruit", str(thread["id"]), "--reason", "Independent file approach", "--key", "one-approach")
                self.assertIsNotNone(invitation["id"])
            if str(agent["id"]) not in initial:
                self.assertIn("Independent file approach", invocation.argv[-1])
                self.assertIn(str(thread["id"]), invocation.argv[-1])
            await self.command(invocation, "reply", str(thread["id"]), "--body", "Independent finding")
            await self.command(invocation, "retire", "--reason", "Contribution posted")
            return 0

        async def scenario():
            return await asyncio.wait_for(run_reactor(forum=self.forum, prepared=prepared, runner=runner,
                                                       poll_interval=.01, max_concurrent=2), 20)

        self.assertEqual([0, 0, 0], asyncio.run(scenario()))
        self.assertEqual(3, len(set(calls)))
        self.assertEqual({self.workspace}, set(paths))
        self.assertFalse((self.forum.state_dir / "runs" / run_id / "workspaces").exists())
        self.assertEqual("baseline\n", (self.workspace / "example.txt").read_text())
        summary = PopulationStore(self.forum, run_id).summary()
        self.assertEqual(3, summary["invocations_started"])
        self.assertEqual(3, summary["total_births"])
        self.assertEqual(0, summary["open_calls"])

    def test_invocation_budget_pauses_and_resume_preserves_usage(self):
        policy = PopulationPolicy(initial_agents=1, max_agents=2, birth_burst=2,
                                  max_births=3, max_invocations=2, participation_grace=0)
        prepared = prepare_run(forum=self.forum, goal="Continue a bounded run", workspace=self.workspace,
                               profiles=self.profiles[:1], population_policy=policy, adaptive=True)
        run_id = str(prepared.run["id"])
        agent_id = str(prepared.peers[0].agent["id"])
        calls = []

        async def runner(**kwargs):
            agent, invocation = kwargs["agent"], kwargs["invocation"]
            calls.append(invocation.cwd)
            self.forum.set_process_state(agent_id, ProcessState.DORMANT, session_id="persisted-fake-session", exit_code=0)
            if len(calls) < 3:
                self.forum.create_thread(run_id, "human", "Next step", f"@{agent['name']} please continue")
            else:
                self.forum.retire_agent(agent_id, "Finished")
            return 0

        async def execute(value):
            return await asyncio.wait_for(run_reactor(forum=self.forum, prepared=value, runner=runner,
                                                       poll_interval=.005, max_concurrent=1), 10)

        asyncio.run(execute(prepared))
        store = PopulationStore(self.forum, run_id)
        self.assertEqual(2, len(calls))
        self.assertEqual(2, store.summary()["invocations_started"])
        self.assertTrue(self.forum.pending_notifications(agent_id))
        from dataclasses import replace
        store.configure(policy=replace(policy, max_invocations=3))
        resumed = prepare_resume(forum=self.forum, run_id=run_id)
        asyncio.run(execute(resumed))
        self.assertEqual(3, len(calls))
        self.assertEqual({self.workspace}, set(calls))
        self.assertEqual(3, store.summary()["invocations_started"])
        self.assertEqual(1, store.summary()["total_births"])
        self.assertEqual([], self.forum.pending_notifications(agent_id))


if __name__ == "__main__":
    unittest.main()
