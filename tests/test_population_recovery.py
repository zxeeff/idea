from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from pathlib import Path

from idea.commands import dispatch_forum
from idea.domain import AgentProfile, Effort, ProcessState, Provider
from idea.execution import ExecutionStore
from idea.forum import Forum
from idea.launcher import prepare_resume, prepare_run, run_reactor
from idea.population import PopulationPolicy, PopulationStore


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

    def test_new_peer_starts_in_original_folder_without_copy_preparation(self) -> None:
        policy = PopulationPolicy(
            initial_agents=1, max_agents=2, birth_burst=2, max_births=2,
            max_invocations=2, participation_grace=0, max_offers_per_call=0,
        )
        prepared = prepare_run(
            forum=self.forum, goal="Collaborate in the selected folder",
            workspace=self.workspace, profiles=self.profiles, population_policy=policy,
            adaptive=True,
        )
        run_id = str(prepared.run["id"])
        initial_id = str(prepared.peers[0].agent["id"])
        discussion = self.forum.create_thread(run_id, "human", "Another approach", "Independent contribution welcome")
        calls = []

        async def runner(**kwargs):
            agent_id = str(kwargs["agent"]["id"])
            invocation = kwargs["invocation"]
            calls.append(agent_id)
            self.assertEqual(self.workspace, invocation.cwd)
            self.assertNotIn("IDEA_BRIDGE_DIR", invocation.env)
            if agent_id == initial_id:
                dispatch_forum(self.forum, run_id, agent_id, "recruit", {
                    "thread_id": str(discussion["id"]),
                    "reason": "An independent document approach",
                    "request_key": "direct-admission",
                })
            self.forum.retire_agent(agent_id, "Contribution finished")
            return 0

        codes = asyncio.run(asyncio.wait_for(run_reactor(
            forum=self.forum, prepared=prepared, runner=runner,
            max_concurrent=2, poll_interval=0.002,
        ), timeout=5))
        self.assertEqual([0, 0], codes)
        self.assertEqual(2, len(set(calls)))
        self.assertEqual(2, PopulationStore(self.forum, run_id).summary()["invocations_started"])
        self.assertFalse((self.forum.state_dir / "runs" / run_id / "workspaces").exists())

    def mark_legacy_isolated(self, run_id: str) -> None:
        with self.forum._connection() as connection:
            connection.execute("CREATE TABLE workspace_policies (run_id TEXT PRIMARY KEY, mode TEXT)")
            connection.execute("CREATE TABLE agent_workspaces (agent_id TEXT PRIMARY KEY, run_id TEXT)")
            connection.execute(
                "INSERT INTO workspace_policies (run_id, mode) VALUES (?, 'isolated')", (run_id,),
            )
            connection.executemany(
                "INSERT INTO agent_workspaces (agent_id, run_id) VALUES (?, ?)",
                [(str(agent["id"]), run_id) for agent in self.forum.list_agents(run_id)],
            )

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

    def test_legacy_isolated_dry_run_keeps_sessions_until_real_resume(self) -> None:
        prepared = self.legacy_run()
        run_id = str(prepared.run["id"])
        sessions = self.old_sessions(prepared)
        self.mark_legacy_isolated(run_id)
        preview = prepare_resume(
            forum=self.forum, run_id=run_id, reset_processes=False,
        )
        for peer in preview.peers:
            agent_id = str(peer.agent["id"])
            self.assertEqual(self.workspace, peer.invocation.cwd)
            self.assertEqual(sessions[agent_id], self.forum.get_agent(agent_id)["session_id"])
            self.assertNotIn(sessions[agent_id], " ".join(peer.invocation.argv))
        resumed = prepare_resume(forum=self.forum, run_id=run_id)
        self.assertEqual(2, len(resumed.peers))
        for peer in resumed.peers:
            agent_id = str(peer.agent["id"])
            self.assertIsNone(self.forum.get_agent(agent_id)["session_id"])
            self.assertEqual(self.workspace, peer.invocation.cwd)
            self.assertNotIn(sessions[agent_id], " ".join(peer.invocation.argv))
        first_id = str(resumed.peers[0].agent["id"])
        self.forum.set_process_state(first_id, ProcessState.DORMANT, session_id="new-shared-session")
        next_resume = prepare_resume(forum=self.forum, run_id=run_id, profile_names=("peer-a",))
        self.assertIn("new-shared-session", next_resume.peers[0].invocation.argv)
        self.assertFalse((self.forum.state_dir / "runs" / run_id / "workspaces").exists())

    def test_legacy_isolated_partial_resume_clears_only_selected_session(self) -> None:
        prepared = self.legacy_run()
        run_id = str(prepared.run["id"])
        sessions = self.old_sessions(prepared)
        self.mark_legacy_isolated(run_id)
        first_id, second_id = [str(peer.agent["id"]) for peer in prepared.peers]
        first = prepare_resume(
            forum=self.forum, run_id=run_id, profile_names=("peer-a",),
        )
        self.assertEqual([first_id], [str(peer.agent["id"]) for peer in first.peers])
        self.assertIsNone(self.forum.get_agent(first_id)["session_id"])
        self.assertEqual(sessions[second_id], self.forum.get_agent(second_id)["session_id"])
        later = prepare_resume(forum=self.forum, run_id=run_id, profile_names=("peer-b",))
        self.assertEqual([second_id], [str(peer.agent["id"]) for peer in later.peers])
        self.assertEqual(self.workspace, later.peers[0].invocation.cwd)
        self.assertIsNone(self.forum.get_agent(second_id)["session_id"])
        self.assertNotIn(sessions[second_id], " ".join(later.peers[0].invocation.argv))
        self.assertFalse((self.forum.state_dir / "runs" / run_id / "workspaces").exists())


if __name__ == "__main__":
    unittest.main()
