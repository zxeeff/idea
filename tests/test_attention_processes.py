from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from idea.domain import AgentProfile, Effort, Provider
from idea.execution import RunLock
from idea.forum import Forum
from idea.providers import Invocation, run_agent


class AttentionProcessesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.forum = Forum(self.root / ".idea")
        self.run = self.forum.create_run("Process lifetime verification", self.root)
        self.profile = AgentProfile("reader", Provider.OPENAI, "test", Effort.HIGH)
        self.agent = self.forum.register_agent(self.run["id"], self.profile)
        self.logs = self.root / "logs"

    def invocation(self, source: str, *, inherited_fds: tuple[int, ...] = ()) -> Invocation:
        return Invocation(argv=(sys.executable, "-c", source), cwd=self.root,
                          env={}, inherited_fds=inherited_fds)

    async def launch(self, invocation: Invocation) -> int:
        return await run_agent(forum=self.forum, run_id=self.run["id"], agent=self.agent,
                               profile=self.profile, invocation=invocation, log_dir=self.logs)

    async def until(self, predicate, timeout: float = 3) -> None:
        deadline = asyncio.get_running_loop().time() + timeout
        while not predicate():
            if asyncio.get_running_loop().time() >= deadline:
                raise TimeoutError("Expected process state did not arrive")
            await asyncio.sleep(0.01)

    @staticmethod
    def running(pid: int) -> bool:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        # On some Unix hosts an orphan remains briefly as a zombie until init
        # reaps it. A zombie cannot execute or retain the run-lock descriptor.
        state = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)],
                               check=False, capture_output=True, text=True).stdout.strip()
        return bool(state) and not state.startswith("Z")

    def lingering_child_source(self, *, keep_cli: bool) -> str:
        child = (
            "import os,signal,time; from pathlib import Path; "
            "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
            "Path('child.pid').write_text(str(os.getpid())); time.sleep(60)"
        )
        return (
            "import os,signal,subprocess,sys,time\nfrom pathlib import Path\n"
            "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
            "Path('cli.pid').write_text(str(os.getpid()))\n"
            f"subprocess.Popen([sys.executable, '-c', {child!r}])\n"
            "while not Path('child.pid').exists(): time.sleep(0.005)\n"
            "print('raw provider output', flush=True)\n"
            + ("time.sleep(60)\n" if keep_cli else "sys.exit(0)\n")
        )

    def test_actual_cli_status_session_and_raw_output_are_preserved(self) -> None:
        event = json.dumps({"thread_id": "provider-session", "value": "raw output"})
        invocation = self.invocation(f"import sys; print({event!r}); sys.exit(7)")
        code = asyncio.run(asyncio.wait_for(self.launch(invocation), timeout=3))
        self.assertEqual(7, code)
        self.assertEqual(event + "\n", (self.logs / "reader.jsonl").read_text())
        self.assertEqual("provider-session", self.forum.get_agent(self.agent["id"])["session_id"])
        self.assertEqual([], list(self.logs.glob(".execution-*")))

    def test_cli_exit_cleans_child_holding_stdout_and_ignoring_term(self) -> None:
        async def scenario():
            code = await asyncio.wait_for(self.launch(self.invocation(
                self.lingering_child_source(keep_cli=False)
            )), timeout=3)
            child_pid = int((self.root / "child.pid").read_text())
            await self.until(lambda: not self.running(child_pid))
            return code

        self.assertEqual(0, asyncio.run(scenario()))
        self.assertEqual("raw provider output\n", (self.logs / "reader.jsonl").read_text())

    def test_repeated_cancellation_kills_term_ignoring_cli_and_tools(self) -> None:
        async def scenario():
            task = asyncio.create_task(self.launch(self.invocation(self.lingering_child_source(keep_cli=True))))
            try:
                await self.until(lambda: (self.root / "child.pid").exists())
                pids = [int((self.root / name).read_text()) for name in ("cli.pid", "child.pid")]
                task.cancel()
                asyncio.get_running_loop().call_later(0.01, task.cancel)
                with self.assertRaises(asyncio.CancelledError):
                    await asyncio.wait_for(task, timeout=3)
                await self.until(lambda: all(not self.running(pid) for pid in pids))
            finally:
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)

        with patch("idea.providers._PROCESS_STOP_GRACE", 0.05):
            asyncio.run(scenario())
        self.assertEqual("failed", self.forum.get_agent(self.agent["id"])["process_state"])

    def test_supervisor_retains_lock_even_if_provider_closes_extra_fds(self) -> None:
        source = (
            "import os,time\nfrom pathlib import Path\n"
            "os.closerange(3, 1024)\n"
            "Path('ready').write_text('ready')\n"
            "while not Path('release').exists(): time.sleep(0.01)\n"
        )

        async def scenario():
            with RunLock(self.forum.state_dir, self.run["id"]) as lock:
                task = asyncio.create_task(self.launch(self.invocation(source, inherited_fds=(lock.fd,))))
                await self.until(lambda: (self.root / "ready").exists())
            try:
                # Closing the parent FD models its disappearance after a crash.
                with self.assertRaisesRegex(RuntimeError, "already has a launcher"):
                    with RunLock(self.forum.state_dir, self.run["id"]):
                        self.fail("Provider lost its supervisor's lock")
                (self.root / "release").write_text("release")
                self.assertEqual(0, await asyncio.wait_for(task, timeout=3))
                with RunLock(self.forum.state_dir, self.run["id"]):
                    pass
            finally:
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)

        asyncio.run(scenario())

    def test_cancellation_during_spawn_waits_for_creation_then_cleans_group(self) -> None:
        original_spawn = asyncio.create_subprocess_exec

        async def scenario():
            release_spawn = asyncio.Event()

            async def delayed_spawn(*args, **kwargs):
                process = await original_spawn(*args, **kwargs)
                await release_spawn.wait()
                return process

            with patch("idea.providers.asyncio.create_subprocess_exec", side_effect=delayed_spawn):
                task = asyncio.create_task(self.launch(self.invocation(self.lingering_child_source(keep_cli=True))))
                try:
                    await self.until(lambda: (self.root / "child.pid").exists())
                    pids = [int((self.root / name).read_text()) for name in ("cli.pid", "child.pid")]
                    task.cancel()
                    asyncio.get_running_loop().call_later(0.01, task.cancel)
                    asyncio.get_running_loop().call_later(0.02, release_spawn.set)
                    with self.assertRaises(asyncio.CancelledError):
                        await asyncio.wait_for(task, timeout=3)
                    await self.until(lambda: all(not self.running(pid) for pid in pids))
                finally:
                    release_spawn.set()
                    if not task.done():
                        task.cancel()
                        await asyncio.gather(task, return_exceptions=True)

        with patch("idea.providers._PROCESS_STOP_GRACE", 0.05):
            asyncio.run(scenario())

    def test_unexpected_supervisor_death_does_not_leave_reader_hung(self) -> None:
        async def scenario():
            task = asyncio.create_task(self.launch(self.invocation(self.lingering_child_source(keep_cli=True))))
            try:
                await self.until(lambda: (self.root / "child.pid").exists())
                pids = [int((self.root / name).read_text()) for name in ("cli.pid", "child.pid")]
                host_pid = int(self.forum.get_agent(self.agent["id"])["pid"])
                os.kill(host_pid, signal.SIGKILL)
                self.assertNotEqual(0, await asyncio.wait_for(task, timeout=3))
                await self.until(lambda: all(not self.running(pid) for pid in pids))
            finally:
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)

        asyncio.run(scenario())

    def test_empty_or_missing_provider_command_is_a_failed_attempt(self) -> None:
        for argv in ((), (str(self.root / "missing-command"),)):
            with self.subTest(argv=argv):
                invocation = Invocation(argv=argv, cwd=self.root, env={})
                self.assertEqual(127, asyncio.run(asyncio.wait_for(self.launch(invocation), timeout=3)))
        self.assertIn("launcher error:", (self.logs / "reader.jsonl").read_text())


if __name__ == "__main__":
    unittest.main()
