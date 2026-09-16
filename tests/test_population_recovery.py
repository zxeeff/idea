from __future__ import annotations

import asyncio
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from idea import launcher
from idea.bridge import BridgeClient
from idea.domain import AgentProfile, Effort, ProcessState, Provider
from idea.execution import ExecutionStore
from idea.forum import Forum
from idea.launcher import prepare_resume, prepare_run, run_reactor
from idea.population import PopulationPolicy, PopulationStore
from idea.workspaces import WorkspaceStore


class PopulationRecoveryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.workspace = Path(self.directory.name).resolve()
        (self.workspace / "document.txt").write_text("Shared starting document\n", encoding="utf-8")
        self.forum = Forum(self.workspace / ".idea-swarm")
        self.profiles = (
            AgentProfile("peer-a", Provider.OPENAI, "fake-codex", Effort.LOW),
            AgentProfile("peer-b", Provider.ANTHROPIC, "fake-claude", Effort.LOW),
        )

    def legacy_run(self, count=2):
        return prepare_run(
            forum=self.forum, goal="Improve a shared document", workspace=self.workspace,
            profiles=self.profiles[:count],
        )

    def old_sessions(self, prepared):
        sessions = {}
        for peer in prepared.peers:
            agent_id = str(peer.agent["id"])
            sessions[agent_id] = "old-shared-session-" + peer.profile.name
            self.forum.set_process_state(
                agent_id, ProcessState.DORMANT, session_id=sessions[agent_id], exit_code=0,
            )
        return sessions

    def record_owned_attempt(self, prepared, agent_id):
        store = ExecutionStore(self.forum, str(prepared.run["id"]))
        policy = store.configure(max_concurrent=2)
        request = store.enqueue(agent_id, kind="start")
        attempt = store.claim(request, [], policy)
        self.assertIsNotNone(attempt)
        return store, request, attempt

    def test_slow_dynamic_preparation_does_not_block_existing_peer_bridge_rpc(self) -> None:
        policy = PopulationPolicy(
            initial_agents=1, max_agents=2, birth_burst=2, max_births=2,
            max_invocations=2, participation_grace=0,
        )
        prepared = prepare_run(
            forum=self.forum, goal="Keep collaborating while another working copy is prepared",
            workspace=self.workspace, profiles=self.profiles, population_policy=policy,
            adaptive=True, workspace_mode="isolated",
        )
        run_id = str(prepared.run["id"])
        initial_id = str(prepared.peers[0].agent["id"])
        discussion = self.forum.create_thread(run_id, "human", "Another approach", "Independent contribution welcome")
        preparation_entered = threading.Event()
        release_preparation = threading.Event()
        preparation_returned = threading.Event()
        actual_register = launcher._registered_peer
        observed = {}
        calls = []
        failures = []

        def slow_register(forum, run, record, **kwargs):
            if str(record["id"]) != initial_id:
                preparation_entered.set()
                if not release_preparation.wait(timeout=4):
                    observed["preparation_timed_out"] = True
                    raise TimeoutError("Existing peer could not post while preparation was blocked")
            result = actual_register(forum, run, record, **kwargs)
            if str(record["id"]) != initial_id:
                preparation_returned.set()
            return result

        async def runner(**kwargs):
            agent_id = str(kwargs["agent"]["id"])
            invocation = kwargs["invocation"]
            calls.append(agent_id)
            self.forum.set_process_state(agent_id, ProcessState.RUNNING, session_id="fake-" + agent_id)
            client = BridgeClient(invocation.env["IDEA_BRIDGE_DIR"], timeout=2, poll_interval=0.002)
            try:
                if agent_id == initial_id:
                    await asyncio.to_thread(client.call, "recruit", {
                        "thread_id": str(discussion["id"]), "reason": "An independent document approach",
                        "request_key": "slow-copy-regression",
                    })
                    entered = await asyncio.to_thread(preparation_entered.wait, 4)
                    if not entered:
                        raise TimeoutError("Dynamic preparation did not start")
                    post = await asyncio.to_thread(client.call, "post", {
                        "title": "Existing peer progressed", "body": "Bridge RPC continued during workspace preparation",
                    })
                    observed["post_id"] = post["id"]
                    observed["post_during_preparation"] = not preparation_returned.is_set() and not release_preparation.is_set()
                    release_preparation.set()
                await asyncio.to_thread(client.call, "retire", {"reason": "Finished the regression contribution"})
                return 0
            except Exception as error:
                failures.append(str(error))
                self.forum.retire_agent(agent_id, "Fake runner failed")
                return 1
            finally:
                if agent_id == initial_id:
                    release_preparation.set()

        async def scenario():
            try:
                return await asyncio.wait_for(run_reactor(
                    forum=self.forum, prepared=prepared, runner=runner,
                    max_concurrent=2, poll_interval=0.002,
                ), timeout=10)
            finally:
                release_preparation.set()

        with patch.object(launcher, "_registered_peer", side_effect=slow_register):
            codes = asyncio.run(scenario())
        self.assertEqual([], failures)
        self.assertEqual([0, 0], codes)
        self.assertEqual(2, len(set(calls)))
        self.assertFalse(observed.get("preparation_timed_out", False))
        self.assertTrue(observed.get("post_during_preparation"))
        self.assertTrue(preparation_returned.is_set())
        self.assertEqual("Existing peer progressed", self.forum.get_thread(observed["post_id"])["title"])
        self.assertEqual(2, PopulationStore(self.forum, run_id).summary()["invocations_started"])

    def test_resume_recovers_owned_attempt_with_reused_pid_but_skips_legacy_live_pid(self) -> None:
        prepared = self.legacy_run()
        tracked_id, legacy_id = [str(peer.agent["id"]) for peer in prepared.peers]
        self.record_owned_attempt(prepared, tracked_id)
        for agent_id in (tracked_id, legacy_id):
            self.forum.set_process_state(
                agent_id, ProcessState.RUNNING, pid=os.getpid(), session_id="session-" + agent_id,
            )
        resumed = prepare_resume(forum=self.forum, run_id=str(prepared.run["id"]))
        self.assertEqual([tracked_id], [str(peer.agent["id"]) for peer in resumed.peers])
        self.assertEqual("created", self.forum.get_agent(tracked_id)["process_state"])
        self.assertIsNone(self.forum.get_agent(tracked_id)["pid"])
        legacy = self.forum.get_agent(legacy_id)
        self.assertEqual("running", legacy["process_state"])
        self.assertEqual(os.getpid(), legacy["pid"])
        self.assertIn("session-" + tracked_id, resumed.peers[0].invocation.argv)

    def test_reactor_recovers_owned_attempt_without_trusting_reused_pid(self) -> None:
        prepared = self.legacy_run(count=1)
        agent_id = str(prepared.peers[0].agent["id"])
        self.record_owned_attempt(prepared, agent_id)
        self.forum.set_process_state(agent_id, ProcessState.RUNNING, pid=os.getpid(), session_id="recoverable-session")
        calls = []

        async def runner(**kwargs):
            calls.append(str(kwargs["agent"]["id"]))
            self.forum.retire_agent(agent_id, "Recovered persisted execution")
            return 0

        codes = asyncio.run(asyncio.wait_for(run_reactor(
            forum=self.forum, prepared=prepared, runner=runner, poll_interval=0.002,
        ), timeout=3))
        self.assertEqual([0], codes)
        self.assertEqual([agent_id], calls)
        with self.forum._connection() as connection:
            states = [row[0] for row in connection.execute(
                """SELECT t.state FROM execution_attempts t JOIN execution_requests r ON r.id=t.request_id
                   WHERE r.agent_id=? ORDER BY t.started_at""", (agent_id,),
            ).fetchall()]
        self.assertEqual(["interrupted", "complete"], states)

    def test_isolated_dry_run_preserves_reset_requirement_until_real_resume(self) -> None:
        prepared = self.legacy_run()
        run_id = str(prepared.run["id"])
        sessions = self.old_sessions(prepared)
        preview = prepare_resume(
            forum=self.forum, run_id=run_id, workspace_mode="isolated", reset_processes=False,
        )
        copies = WorkspaceStore(self.forum, run_id)
        preview_paths = {}
        for peer in preview.peers:
            agent_id = str(peer.agent["id"])
            preview_paths[agent_id] = peer.invocation.cwd
            self.assertNotEqual(self.workspace, peer.invocation.cwd)
            self.assertEqual(sessions[agent_id], self.forum.get_agent(agent_id)["session_id"])
            self.assertTrue(copies.fresh_session_required(agent_id))
            self.assertNotIn(sessions[agent_id], " ".join(peer.invocation.argv))
        resumed = prepare_resume(forum=self.forum, run_id=run_id)
        self.assertEqual(2, len(resumed.peers))
        for peer in resumed.peers:
            agent_id = str(peer.agent["id"])
            self.assertIsNone(self.forum.get_agent(agent_id)["session_id"])
            self.assertFalse(copies.fresh_session_required(agent_id))
            self.assertEqual(preview_paths[agent_id], peer.invocation.cwd)
            self.assertNotIn(sessions[agent_id], " ".join(peer.invocation.argv))

    def test_partial_transition_clears_later_peer_session_when_it_first_gets_private_copy(self) -> None:
        prepared = self.legacy_run()
        run_id = str(prepared.run["id"])
        sessions = self.old_sessions(prepared)
        first_id, second_id = [str(peer.agent["id"]) for peer in prepared.peers]
        first = prepare_resume(
            forum=self.forum, run_id=run_id, profile_names=("peer-a",), workspace_mode="isolated",
        )
        self.assertEqual([first_id], [str(peer.agent["id"]) for peer in first.peers])
        self.assertIsNone(self.forum.get_agent(first_id)["session_id"])
        self.assertEqual(sessions[second_id], self.forum.get_agent(second_id)["session_id"])
        later = prepare_resume(forum=self.forum, run_id=run_id, profile_names=("peer-b",))
        self.assertEqual([second_id], [str(peer.agent["id"]) for peer in later.peers])
        self.assertNotEqual(self.workspace, later.peers[0].invocation.cwd)
        self.assertIsNone(self.forum.get_agent(second_id)["session_id"])
        self.assertNotIn(sessions[second_id], " ".join(later.peers[0].invocation.argv))
        copies = WorkspaceStore(self.forum, run_id)
        self.assertFalse(copies.fresh_session_required(first_id))
        self.assertFalse(copies.fresh_session_required(second_id))


if __name__ == "__main__":
    unittest.main()
