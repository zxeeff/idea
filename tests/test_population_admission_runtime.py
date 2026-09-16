from __future__ import annotations

import asyncio
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from idea.bridge import BridgeClient
from idea.domain import AgentProfile, Effort, ProcessState, Provider
from idea.execution import ExecutionStore
from idea.forum import Forum
from idea.launcher import prepare_resume, prepare_run, run_reactor
from idea.population import PopulationPolicy, PopulationStore
from idea.workspaces import WorkspaceStore


class PopulationAdmissionRuntimeTest(unittest.TestCase):
    """Exercise population admission with real storage/mailboxes and fake models."""

    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.workspace = Path(self.directory.name).resolve()
        (self.workspace / "example.txt").write_text("unchanged origin\n", encoding="utf-8")
        self.forum = Forum(self.workspace / ".idea-swarm")
        self.profiles = (
            AgentProfile("codex-template", Provider.OPENAI, "fake-codex", Effort.LOW),
            AgentProfile("claude-template", Provider.ANTHROPIC, "fake-claude", Effort.HIGH),
        )

    def seeded_run(self, *, count: int = 2, extra_invocations: int = 1):
        policy = PopulationPolicy(
            initial_agents=count, max_agents=6, birth_burst=6, max_births=10,
            max_invocations=count + extra_invocations, participation_grace=0,
            max_open_calls_per_agent=4, max_offers_per_call=2,
            offer_cooldown=300, idle_timeout=300,
        )
        prepared = prepare_run(
            forum=self.forum, goal="Reuse existing participants before adding another",
            workspace=self.workspace, profiles=self.profiles, population_policy=policy,
            adaptive=True, workspace_mode="isolated",
        )
        run_id = str(prepared.run["id"])
        population = PopulationStore(self.forum, run_id)
        execution = ExecutionStore(self.forum, run_id, population=population)
        execution_policy = execution.configure(max_concurrent=3)
        sessions = {}
        # Give each prepared peer one completed, accounted invocation. Nothing
        # launches here; subsequent invitations must reuse these same sessions.
        for peer in prepared.peers:
            agent_id = str(peer.agent["id"])
            request = execution.enqueue(agent_id, kind="start")
            attempt = execution.claim(request, [], execution_policy)
            self.assertIsNotNone(attempt)
            sessions[agent_id] = "retained-session-" + agent_id
            self.forum.set_process_state(
                agent_id, ProcessState.DORMANT, session_id=sessions[agent_id], exit_code=0,
            )
            execution.finish(request, str(attempt), exit_code=0)
        self.park([str(peer.agent["id"]) for peer in prepared.peers])
        return prepared, population, sessions

    def park(self, agent_ids: list[str]) -> None:
        # A stale observation removes real-time sleeps from the setup. Normal
        # transitions into/out of this state are exercised by the reactor below.
        with self.forum._connection() as connection:
            connection.executemany(
                """UPDATE agents SET participation_state='parked',
                   parked_at='2000-01-01T00:00:00+00:00',
                   exited_at='2000-01-01T00:00:00+00:00' WHERE id=?""",
                [(agent_id,) for agent_id in agent_ids],
            )

    def recruitment(self, prepared, population):
        requester = next(peer for peer in prepared.peers if peer.profile.provider is Provider.OPENAI)
        existing = next(peer for peer in prepared.peers if peer.profile.provider is Provider.ANTHROPIC)
        thread = self.forum.create_thread(str(prepared.run["id"]), "human", "Public invitation", "Another view is welcome")
        template = next(item for item in population.templates() if item["provider"] == "anthropic")
        call = population.open_call(
            str(requester.agent["id"]), str(thread["id"]), "Consider the existing evidence",
            template_id=template["id"], request_key="one-independent-view",
        )
        return requester, existing, call

    async def volunteer(self, invocation, call_id: str):
        client = BridgeClient(invocation.env["IDEA_BRIDGE_DIR"], timeout=2, poll_interval=.002)
        return await asyncio.to_thread(client.call, "volunteer", {"call_id": call_id})

    async def execute(self, prepared, runner, *, max_concurrent=2, max_codex=None):
        # Surface fake-runner assertion failures immediately: the production
        # reactor deliberately converts ordinary runner exceptions to failures.
        failure = asyncio.get_running_loop().create_future()

        async def guarded(**kwargs):
            try:
                return await runner(**kwargs)
            except Exception as error:
                if not failure.done():
                    failure.set_result(error)
                raise

        task = asyncio.create_task(run_reactor(
            forum=self.forum, prepared=prepared, runner=guarded, poll_interval=.005,
            max_concurrent=max_concurrent, max_codex=max_codex,
        ))
        try:
            done, _ = await asyncio.wait({task, failure}, timeout=10, return_when=asyncio.FIRST_COMPLETED)
            if failure in done:
                raise failure.result()
            self.assertIn(task, done, "population reactor did not reach its bounded stopping condition")
            return await task
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            if not failure.done():
                failure.cancel()

    def test_parked_peers_do_not_start_without_new_demand(self) -> None:
        prepared, population, sessions = self.seeded_run()
        run_id = str(prepared.run["id"])
        population.configure(policy=replace(population.policy(), idle_timeout=.01))
        before = population.summary()["invocations_started"]
        resumed = prepare_resume(forum=self.forum, run_id=run_id)
        calls = []

        async def runner(**kwargs):
            calls.append(str(kwargs["agent"]["id"]))
            raise AssertionError("a parked peer was started without demand")

        async def scenario():
            cycles_observed = asyncio.Event()
            cycles = 0
            real_park = PopulationStore.park_idle_peers

            def observe_parking(store, *args, **kwargs):
                nonlocal cycles
                result = real_park(store, *args, **kwargs)
                cycles += 1
                if cycles >= 3:
                    cycles_observed.set()
                return result

            with patch.object(PopulationStore, "park_idle_peers", observe_parking):
                task = asyncio.create_task(run_reactor(
                    forum=self.forum, prepared=resumed, runner=runner, poll_interval=.005,
                ))
                try:
                    await asyncio.wait_for(cycles_observed.wait(), 3)
                finally:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)

        asyncio.run(scenario())
        self.assertEqual([], calls)
        self.assertEqual(before, population.summary()["invocations_started"])
        for agent_id, session in sessions.items():
            record = self.forum.get_agent(agent_id)
            self.assertEqual("parked", record["participation_state"])
            self.assertEqual(session, record["session_id"])
            self.assertEqual("dormant", record["process_state"])

    def test_idle_peer_accepts_an_offer_in_the_same_session_and_working_copy(self) -> None:
        prepared, population, sessions = self.seeded_run()
        run_id = str(prepared.run["id"])
        _, existing, call = self.recruitment(prepared, population)
        existing_id = str(existing.agent["id"])
        copies = WorkspaceStore(self.forum, run_id)
        original_copy = Path(copies.get(existing_id)["path"])
        (original_copy / "example.txt").write_text("retained private edits\n", encoding="utf-8")
        resumed = prepare_resume(forum=self.forum, run_id=run_id)
        calls = []

        async def runner(**kwargs):
            agent_id, invocation = str(kwargs["agent"]["id"]), kwargs["invocation"]
            calls.append(agent_id)
            self.assertEqual(existing_id, agent_id)
            self.assertEqual(existing.profile, kwargs["profile"])
            self.assertEqual(original_copy, invocation.cwd)
            self.assertIn(sessions[agent_id], invocation.argv)
            self.assertEqual("retained private edits\n", (invocation.cwd / "example.txt").read_text())
            self.assertEqual(["invitation"], [item["notification_reason"] for item in self.forum.pending_notifications(agent_id)])
            self.assertNotIn("IDEA_TRIGGER_EVENT_ID", invocation.env)
            accepted = await self.volunteer(invocation, call["id"])
            self.assertEqual("volunteer", accepted["fulfillment_kind"])
            self.forum.set_process_state(agent_id, ProcessState.DORMANT, session_id=sessions[agent_id], exit_code=0)
            return 0

        asyncio.run(self.execute(resumed, runner))
        self.assertEqual([existing_id], calls)
        self.assertEqual(2, population.summary()["total_births"])
        self.assertEqual(3, population.summary()["invocations_started"])
        self.assertEqual("volunteer", population.get_call(call["id"])["fulfillment_kind"])
        self.assertEqual("unchanged origin\n", (self.workspace / "example.txt").read_text())

    def test_failed_or_blocked_invitation_does_not_restart_the_same_peer(self) -> None:
        for failed_state in (ProcessState.FAILED, ProcessState.BLOCKED):
            with self.subTest(state=failed_state.value):
                prepared, population, sessions = self.seeded_run(extra_invocations=2)
                run_id = str(prepared.run["id"])
                _, existing, call = self.recruitment(prepared, population)
                existing_id = str(existing.agent["id"])
                initial_ids = {str(peer.agent["id"]) for peer in prepared.peers}
                resumed = prepare_resume(forum=self.forum, run_id=run_id)
                calls = []

                async def runner(**kwargs):
                    agent_id, invocation = str(kwargs["agent"]["id"]), kwargs["invocation"]
                    calls.append(agent_id)
                    if agent_id == existing_id:
                        self.assertEqual(1, calls.count(existing_id), "an old invitation retried its failed recipient")
                        self.assertIn(sessions[agent_id], invocation.argv)
                        self.assertNotIn("IDEA_TRIGGER_EVENT_ID", invocation.env)
                        exit_code = 0 if failed_state is ProcessState.BLOCKED else 1
                        self.forum.set_process_state(agent_id, failed_state, session_id=sessions[agent_id], exit_code=exit_code)
                        return exit_code
                    self.assertNotIn(agent_id, initial_ids)
                    self.assertEqual(Provider.ANTHROPIC, kwargs["profile"].provider)
                    self.forum.retire_agent(agent_id, "Replacement recorded its independent participation")
                    return 0

                asyncio.run(self.execute(resumed, runner))
                self.assertEqual(1, calls.count(existing_id))
                self.assertEqual(2, len(calls))
                self.assertEqual(failed_state.value, self.forum.get_agent(existing_id)["process_state"])
                self.assertEqual(sessions[existing_id], self.forum.get_agent(existing_id)["session_id"])
                self.assertEqual(3, population.summary()["total_births"])
                self.assertEqual("new_peer", population.get_call(call["id"])["fulfillment_kind"])

    def test_codex_backlog_does_not_block_an_available_claude_slot(self) -> None:
        prepared, population, sessions = self.seeded_run(count=3, extra_invocations=3)
        run_id = str(prepared.run["id"])
        _, existing, call = self.recruitment(prepared, population)
        codex_ids = [str(peer.agent["id"]) for peer in prepared.peers if peer.profile.provider is Provider.OPENAI]
        existing_id = str(existing.agent["id"])
        with self.forum._connection() as connection:
            connection.executemany("UPDATE agents SET participation_state='resident',parked_at=NULL WHERE id=?",
                                   [(agent_id,) for agent_id in codex_ids])
        resumed = prepare_resume(forum=self.forum, run_id=run_id)
        calls = []

        async def scenario():
            claude_finished = asyncio.Event()

            async def runner(**kwargs):
                agent_id, invocation = str(kwargs["agent"]["id"]), kwargs["invocation"]
                calls.append(agent_id)
                self.forum.set_process_state(agent_id, ProcessState.RUNNING, session_id=sessions[agent_id])
                if agent_id == codex_ids[0]:
                    await asyncio.wait_for(claude_finished.wait(), 3)
                elif agent_id == existing_id:
                    self.assertIn(codex_ids[0], calls)
                    self.assertNotIn(codex_ids[1], calls)
                    self.assertIn(sessions[agent_id], invocation.argv)
                    await self.volunteer(invocation, call["id"])
                    claude_finished.set()
                else:
                    self.assertEqual(codex_ids[1], agent_id)
                    self.assertTrue(claude_finished.is_set())
                self.forum.set_process_state(agent_id, ProcessState.DORMANT, session_id=sessions[agent_id], exit_code=0)
                return 0

            await self.execute(resumed, runner, max_concurrent=2, max_codex=1)

        asyncio.run(scenario())
        self.assertEqual({existing_id, *codex_ids}, set(calls))
        self.assertEqual(3, len(calls))
        self.assertEqual(3, population.summary()["total_births"])

    def test_pending_human_message_is_delivered_before_an_offer_to_that_peer(self) -> None:
        prepared, population, sessions = self.seeded_run(extra_invocations=2)
        run_id = str(prepared.run["id"])
        _, existing, call = self.recruitment(prepared, population)
        existing_id = str(existing.agent["id"])
        self.forum.create_thread(run_id, "human", "Existing conversation", f"@{existing.profile.name} please read this first")
        resumed = prepare_resume(forum=self.forum, run_id=run_id)
        deliveries = []

        async def runner(**kwargs):
            agent_id, invocation = str(kwargs["agent"]["id"]), kwargs["invocation"]
            self.assertEqual(existing_id, agent_id)
            self.assertIn(sessions[agent_id], invocation.argv)
            reasons = [item["notification_reason"] for item in self.forum.pending_notifications(agent_id)]
            deliveries.append(reasons)
            if len(deliveries) == 1:
                self.assertEqual(["mention"], reasons)
                self.assertIn("IDEA_TRIGGER_EVENT_ID", invocation.env)
            else:
                self.assertEqual(2, len(deliveries))
                self.assertEqual(["invitation"], reasons)
                self.assertNotIn("IDEA_TRIGGER_EVENT_ID", invocation.env)
                await self.volunteer(invocation, call["id"])
            self.forum.set_process_state(agent_id, ProcessState.DORMANT, session_id=sessions[agent_id], exit_code=0)
            return 0

        asyncio.run(self.execute(resumed, runner, max_concurrent=1))
        self.assertEqual([["mention"], ["invitation"]], deliveries)
        self.assertEqual(2, population.summary()["total_births"])
        self.assertEqual([], self.forum.pending_notifications(existing_id))


if __name__ == "__main__":
    unittest.main()
