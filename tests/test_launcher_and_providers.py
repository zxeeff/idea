from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from idea.domain import AgentProfile, Effort, ProcessState, Provider
from idea.forum import Forum
from idea.launcher import prepare_resume, prepare_run, run_reactor
from idea.profiles import default_profiles
from idea.providers import Invocation, agent_environment, run_agent


class LauncherAndProvidersTest(unittest.TestCase):
    def test_web_password_is_not_inherited_by_provider_processes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {"IDEA_WEB_PASSWORD": "do-not-leak"}):
                env = agent_environment(
                    state_dir=Path(directory),
                    run_id="run-id",
                    agent={"id": "agent-id", "name": "peer"},
                )
        self.assertNotIn("IDEA_WEB_PASSWORD", env)
        self.assertEqual("run-id", env["IDEA_RUN_ID"])

    def test_all_peers_share_workspace_and_are_prepared_without_stages(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            forum = Forum(workspace / ".idea")
            profiles = (
                AgentProfile("fast", Provider.OPENAI, "gpt-5.6-luna", Effort.LOW),
                AgentProfile("deep", Provider.ANTHROPIC, "opus", Effort.MAX),
            )
            prepared = prepare_run(
                forum=forum,
                goal="fix the failing test",
                workspace=workspace,
                profiles=profiles,
            )
            self.assertEqual(2, len(prepared.peers))
            self.assertTrue(all(peer.invocation.cwd == workspace.resolve() for peer in prepared.peers))
            self.assertEqual(1, len(forum.list_threads(prepared.run["id"])))

            codex = prepared.peers[0].invocation.argv
            self.assertIn("gpt-5.6-luna", codex)
            self.assertIn('model_reasoning_effort="low"', codex)
            self.assertIn("--json", codex)
            self.assertIn("--dangerously-bypass-approvals-and-sandbox", codex)
            self.assertNotIn("--sandbox", codex)
            codex_system = next(
                value for value in codex if value.startswith("developer_instructions=")
            )
            self.assertIn("forum --help", codex_system)
            self.assertNotIn("fix the failing test", codex_system)
            self.assertNotIn("gpt-5.6-luna", codex_system)
            self.assertNotIn("effort", codex_system.casefold())
            self.assertNotIn(str(workspace.resolve()), codex_system)
            self.assertEqual(1, codex[-1].count("fix the failing test"))

            claude = prepared.peers[1].invocation.argv
            self.assertIn("opus", claude)
            self.assertEqual("max", claude[claude.index("--effort") + 1])
            self.assertIn("--dangerously-skip-permissions", claude)
            self.assertNotIn("--permission-mode", claude)
            claude_system = claude[claude.index("--append-system-prompt") + 1]
            self.assertIn("forum --help", claude_system)
            self.assertNotIn("fix the failing test", claude_system)
            self.assertNotIn("opus", claude_system)
            self.assertNotIn("effort", claude_system.casefold())
            self.assertNotIn(str(workspace.resolve()), claude_system)
            self.assertEqual(1, claude[-1].count("fix the failing test"))

            forum.set_process_state(
                prepared.peers[0].agent["id"],
                ProcessState.FAILED,
                exit_code=130,
                session_id="codex-session-id",
            )
            forum.set_process_state(
                prepared.peers[1].agent["id"],
                ProcessState.FAILED,
                exit_code=143,
                session_id="claude-session-id",
            )
            resumed = prepare_resume(forum=forum, run_id=prepared.run["id"])
            resumed_codex = resumed.peers[0].invocation.argv
            self.assertEqual("resume", resumed_codex[2])
            self.assertIn("codex-session-id", resumed_codex)
            self.assertIn("--dangerously-bypass-approvals-and-sandbox", resumed_codex)
            resumed_claude = resumed.peers[1].invocation.argv
            self.assertEqual(
                "claude-session-id",
                resumed_claude[resumed_claude.index("--resume") + 1],
            )
            self.assertIn("--dangerously-skip-permissions", resumed_claude)

    def test_runner_accepts_an_oversized_jsonl_line_without_dying(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory).resolve()
            forum = Forum(workspace / ".idea")
            profile = AgentProfile("fake", Provider.OPENAI, "fake-model", Effort.LOW)
            run = forum.create_run("goal", workspace)
            agent = forum.register_agent(run["id"], profile)
            event = json.dumps(
                {
                    "thread_id": "session-from-peer",
                    "type": "tool-output",
                    "payload": "x" * 200_000,
                }
            )
            invocation = Invocation(
                argv=(sys.executable, "-c", f"print({event!r})"),
                cwd=workspace,
                env={},
            )
            code = asyncio.run(
                run_agent(
                    forum=forum,
                    run_id=run["id"],
                    agent=agent,
                    profile=profile,
                    invocation=invocation,
                    log_dir=workspace / "logs",
                )
            )
            loaded = forum.get_agent(agent["id"])
            self.assertEqual(0, code)
            self.assertEqual("dormant", loaded["process_state"])
            self.assertEqual("session-from-peer", loaded["session_id"])
            log = (workspace / "logs" / "fake.jsonl").read_text()
            self.assertGreater(len(log), 200_000)
            self.assertIn("session-from-peer", log)

    def test_final_claude_safeguard_refusal_is_blocked_not_generic_failed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory).resolve()
            forum = Forum(workspace / ".idea")
            profile = AgentProfile("guarded", Provider.ANTHROPIC, "opus", Effort.MAX)
            run = forum.create_run("goal", workspace)
            agent = forum.register_agent(run["id"], profile)
            event = json.dumps(
                {
                    "type": "result",
                    "session_id": "blocked-session",
                    "stop_reason": "refusal",
                    "is_error": True,
                }
            )
            invocation = Invocation(
                argv=(sys.executable, "-c", f"import sys; print({event!r}); sys.exit(1)"),
                cwd=workspace,
                env={},
            )
            code = asyncio.run(
                run_agent(
                    forum=forum,
                    run_id=run["id"],
                    agent=agent,
                    profile=profile,
                    invocation=invocation,
                    log_dir=workspace / "logs",
                )
            )
            loaded = forum.get_agent(agent["id"])
            self.assertEqual(1, code)
            self.assertEqual("blocked", loaded["process_state"])
            self.assertEqual("blocked-session", loaded["session_id"])

    def test_explicit_resume_restarts_a_blocked_peer_with_a_fresh_session(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory).resolve()
            forum = Forum(workspace / ".idea")
            profile = AgentProfile("guarded", Provider.ANTHROPIC, "opus", Effort.MAX)
            prepared = prepare_run(
                forum=forum,
                goal="goal",
                workspace=workspace,
                profiles=(profile,),
            )
            agent_id = prepared.peers[0].agent["id"]
            forum.set_process_state(
                agent_id,
                ProcessState.BLOCKED,
                exit_code=1,
                session_id="doomed-session",
            )

            resumed = prepare_resume(forum=forum, run_id=prepared.run["id"])
            argv = resumed.peers[0].invocation.argv
            self.assertNotIn("--resume", argv)
            self.assertNotIn("doomed-session", argv)
            self.assertIn("fresh provider session", argv[-1])
            self.assertIsNone(forum.get_agent(agent_id)["session_id"])

    def test_resume_upgrades_a_legacy_failed_refusal_to_fresh_restart(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory).resolve()
            forum = Forum(workspace / ".idea")
            profile = AgentProfile("legacy-guarded", Provider.ANTHROPIC, "opus", Effort.HIGH)
            prepared = prepare_run(
                forum=forum,
                goal="goal",
                workspace=workspace,
                profiles=(profile,),
            )
            agent_id = prepared.peers[0].agent["id"]
            forum.set_process_state(
                agent_id,
                ProcessState.FAILED,
                exit_code=1,
                session_id="legacy-doomed-session",
            )
            log_dir = forum.state_dir / "runs" / prepared.run["id"] / "logs"
            log_dir.mkdir(parents=True)
            (log_dir / "legacy-guarded.jsonl").write_text(
                '{"type":"system","subtype":"model_refusal_no_fallback"}\n',
                encoding="utf-8",
            )

            resumed = prepare_resume(forum=forum, run_id=prepared.run["id"])
            argv = resumed.peers[0].invocation.argv
            self.assertNotIn("--resume", argv)
            self.assertNotIn("legacy-doomed-session", argv)
            self.assertIn("fresh provider session", argv[-1])

    def test_existing_default_run_can_add_new_top_tier_peers(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory).resolve()
            forum = Forum(workspace / ".idea")
            current = default_profiles()
            # Simulate an older run created before every other profile existed.
            introduced = {profile.name for profile in current[1::2]}
            previous_defaults = tuple(
                profile for profile in current if profile.name not in introduced
            )
            prepared = prepare_run(
                forum=forum,
                goal="goal",
                workspace=workspace,
                profiles=previous_defaults,
            )
            expanded = prepare_resume(
                forum=forum,
                run_id=prepared.run["id"],
                additional_profiles=current,
            )
            names = {peer.profile.name for peer in expanded.peers}
            self.assertTrue(introduced <= names)
            self.assertEqual(len(current), len(forum.list_agents(prepared.run["id"])))

    def test_reactor_wakes_a_fast_dormant_peer_after_slow_peer_posts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory).resolve()
            forum = Forum(workspace / ".idea")
            profiles = (
                AgentProfile("fast", Provider.OPENAI, "gpt-5.6-luna", Effort.LOW),
                AgentProfile("slow", Provider.ANTHROPIC, "opus", Effort.MAX),
            )
            prepared = prepare_run(
                forum=forum,
                goal="fix the failing test",
                workspace=workspace,
                profiles=profiles,
            )
            calls = {"fast": 0, "slow": 0}
            wake_prompts: list[str] = []
            wake_environments: list[dict[str, str]] = []
            passive_thread: dict[str, str] = {}
            late_thread: dict[str, str] = {}

            async def fake_runner(**kwargs):
                agent = kwargs["agent"]
                profile = kwargs["profile"]
                calls[profile.name] += 1
                forum.set_process_state(agent["id"], ProcessState.RUNNING)
                await asyncio.sleep(0)
                if profile.name == "slow":
                    created = forum.create_thread(
                        prepared.run["id"], "slow", "passive progress", "intermediate note"
                    )
                    passive_thread["id"] = created["id"]
                    await asyncio.sleep(0.05)
                    self.assertEqual(1, calls["fast"])
                    created = forum.create_thread(
                        prepared.run["id"],
                        "slow",
                        "late deep result",
                        "new useful chain @fast",
                    )
                    late_thread["id"] = created["id"]
                    forum.retire_agent(agent["id"], "deep result posted")
                elif calls["fast"] == 1:
                    forum.set_process_state(agent["id"], ProcessState.DORMANT, exit_code=0)
                else:
                    wake_prompts.append(kwargs["invocation"].argv[-1])
                    wake_environments.append(kwargs["invocation"].env)
                    forum.retire_agent(agent["id"], "responded to late result")
                return 0

            codes = asyncio.run(
                asyncio.wait_for(
                    run_reactor(
                        forum=forum,
                        prepared=prepared,
                        runner=fake_runner,
                        poll_interval=0.01,
                    ),
                    timeout=2,
                )
            )
            self.assertEqual([0, 0], codes)
            self.assertEqual(2, calls["fast"])
            self.assertEqual(1, calls["slow"])
            self.assertIn("TRIGGERING MENTIONS", wake_prompts[0])
            self.assertIn('"thread_title": "late deep result"', wake_prompts[0])
            self.assertIn('"message": "new useful chain @fast"', wake_prompts[0])
            self.assertIn("BACKGROUND ACTIVITY", wake_prompts[0])
            background = wake_prompts[0].split("BACKGROUND ACTIVITY", 1)[1]
            self.assertIn(passive_thread["id"], background)
            self.assertNotIn(late_thread["id"], background)
            self.assertEqual(
                late_thread["id"], wake_environments[0]["IDEA_TRIGGER_THREAD_ID"]
            )
            self.assertTrue(wake_environments[0]["IDEA_TRIGGER_EVENT_ID"].isdigit())

    def test_all_restarts_a_blocked_peer_fresh_without_resuming_doomed_session(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory).resolve()
            forum = Forum(workspace / ".idea")
            profiles = (
                AgentProfile("guarded", Provider.ANTHROPIC, "opus", Effort.MAX),
                AgentProfile("announcer", Provider.OPENAI, "gpt-5.6-luna", Effort.LOW),
            )
            prepared = prepare_run(
                forum=forum,
                goal="goal",
                workspace=workspace,
                profiles=profiles,
            )
            calls = {"guarded": 0, "announcer": 0}

            async def fake_runner(**kwargs):
                agent = kwargs["agent"]
                profile = kwargs["profile"]
                calls[profile.name] += 1
                forum.set_process_state(agent["id"], ProcessState.RUNNING)
                await asyncio.sleep(0)
                if profile.name == "guarded" and calls["guarded"] == 1:
                    forum.set_process_state(
                        agent["id"],
                        ProcessState.BLOCKED,
                        exit_code=1,
                        session_id="doomed-session",
                    )
                    return 1
                if profile.name == "guarded":
                    argv = kwargs["invocation"].argv
                    self.assertNotIn("--resume", argv)
                    self.assertNotIn("doomed-session", argv)
                    self.assertIn("fresh provider session", argv[-1])
                    forum.retire_agent(agent["id"], "fresh restart verified")
                    return 0
                await asyncio.sleep(0.05)
                forum.create_thread(
                    prepared.run["id"], "announcer", "explicit broadcast", "review this @all"
                )
                forum.retire_agent(agent["id"], "announcement posted")
                return 0

            codes = asyncio.run(
                asyncio.wait_for(
                    run_reactor(
                        forum=forum,
                        prepared=prepared,
                        runner=fake_runner,
                        poll_interval=0.01,
                    ),
                    timeout=2,
                )
            )
            self.assertEqual([0, 0], codes)
            self.assertEqual(2, calls["guarded"])
            self.assertEqual(1, calls["announcer"])


if __name__ == "__main__":
    unittest.main()
