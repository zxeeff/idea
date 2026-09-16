from __future__ import annotations

import asyncio
import json
import os
import re
import signal
import shutil
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .domain import AgentProfile, ProcessState, Provider
from .forum import Forum


_CLAUDE_PEER_TOOLS = "Bash,Edit,Glob,Grep,Read,Write,WebFetch,WebSearch"
_IDEA_MCP_TOOLS = ("post", "reply", "forum")
_IDEA_MCP_ENVIRONMENT = (
    "PYTHONPATH", "IDEA_STATE_DIR", "IDEA_RUN_ID", "IDEA_AGENT_ID", "IDEA_AGENT_NAME",
)


@dataclass(frozen=True, slots=True)
class Invocation:
    argv: tuple[str, ...]
    cwd: Path
    env: dict[str, str]
    inherited_fds: tuple[int, ...] = ()


def _module_root() -> Path:
    return Path(__file__).resolve().parents[1]


def validate_workspace_boundary(*, workspace: Path, state_dir: Path) -> tuple[Path, Path]:
    """Keep shared run state under the original workspace's storage layout."""

    workspace = workspace.expanduser().resolve()
    state_dir = state_dir.expanduser().resolve()
    if not state_dir.is_relative_to(workspace):
        raise ValueError(
            "IDEA requires the forum state directory to be inside the workspace: "
            f"{state_dir} is outside {workspace}"
        )
    return workspace, state_dir


def _mcp_environment(env: dict[str, str]) -> dict[str, str]:
    return {name: env[name] for name in _IDEA_MCP_ENVIRONMENT if name in env}


def _codex_execution_args(workspace: Path, env: dict[str, str]) -> tuple[str, ...]:
    base = (
        "--dangerously-bypass-approvals-and-sandbox",
        "--strict-config",
        "--ignore-user-config",
        "--ignore-rules",
        "--config",
        (
            # --config splits its key on dots, including dots inside quoted
            # path components such as .idea-swarm. Parse paths as TOML values.
            'projects={' + json.dumps(str(workspace), ensure_ascii=False)
            + '={trust_level="untrusted"}}'
        ),
        "--config",
        'approval_policy="never"',
        "--config",
        "shell_environment_policy.ignore_default_excludes=false",
        "--config",
        "mcp_servers.idea.command=" + json.dumps(sys.executable, ensure_ascii=False),
        "--config",
        'mcp_servers.idea.args=["-m","idea.mcp_server"]',
        "--config",
        "mcp_servers.idea.enabled=true",
        "--config",
        "mcp_servers.idea.required=true",
        "--config",
        "mcp_servers.idea.enabled_tools=" + json.dumps(list(_IDEA_MCP_TOOLS), separators=(",", ":")),
        "--config",
        "mcp_servers.idea.startup_timeout_sec=10",
        "--config",
        "mcp_servers.idea.tool_timeout_sec=45",
        "--config",
        'mcp_servers.idea.default_tools_approval_mode="approve"',
    )
    explicit_environment = tuple(
        value
        for name, item in _mcp_environment(env).items()
        for value in (
            "--config",
            f"mcp_servers.idea.env.{name}=" + json.dumps(item, ensure_ascii=False),
        )
    )
    return base + explicit_environment


def _claude_execution_settings() -> str:
    settings = {
        "disableAllHooks": True,
        "sandbox": {"enabled": False},
    }
    return json.dumps(settings, ensure_ascii=False, separators=(",", ":"))


def _claude_mcp_config(env: dict[str, str]) -> str:
    return json.dumps({
        "mcpServers": {
            "idea": {
                "type": "stdio",
                "command": sys.executable,
                "args": ["-m", "idea.mcp_server"],
                "env": _mcp_environment(env),
            }
        }
    }, ensure_ascii=False, separators=(",", ":"))


def agent_environment(
    *,
    state_dir: Path,
    run_id: str,
    agent: dict[str, Any],
) -> dict[str, str]:
    env = os.environ.copy()
    # The browser password belongs to the launcher process. Never expose it to
    # autonomous provider subprocesses.
    env.pop("IDEA_WEB_PASSWORD", None)
    existing_pythonpath = env.get("PYTHONPATH")
    module_root = str(_module_root())
    env["PYTHONPATH"] = (
        module_root if not existing_pythonpath else os.pathsep.join((module_root, existing_pythonpath))
    )
    env.update(
        {
            "IDEA_STATE_DIR": str(state_dir),
            "IDEA_RUN_ID": str(run_id),
            "IDEA_AGENT_ID": str(agent["id"]),
            "IDEA_AGENT_NAME": str(agent["name"]),
            "IDEA_PYTHON": sys.executable,
        }
    )
    return env


def build_invocation(
    *,
    profile: AgentProfile,
    system_prompt: str,
    task_prompt: str,
    workspace: Path,
    state_dir: Path,
    run_id: str,
    agent: dict[str, Any],
    resume_session_id: str | None = None,
) -> Invocation:
    workspace, state_dir = validate_workspace_boundary(workspace=workspace, state_dir=state_dir)
    env = agent_environment(
        state_dir=state_dir,
        run_id=run_id,
        agent=agent,
    )
    if profile.provider is Provider.OPENAI:
        executable = shutil.which("codex") or "codex"
        common = (
            "--model",
            profile.model,
            "--config",
            f'model_reasoning_effort="{profile.effort.value}"',
            "--config",
            f"developer_instructions={json.dumps(system_prompt, ensure_ascii=False)}",
            *_codex_execution_args(workspace, env),
        )
        if resume_session_id:
            argv = (
                executable,
                "exec",
                "resume",
                *common,
                "--skip-git-repo-check",
                "--json",
                resume_session_id,
                task_prompt,
            )
        else:
            argv = (
                executable,
                "exec",
                *common,
                "--cd",
                str(workspace),
                "--skip-git-repo-check",
                "--json",
                task_prompt,
            )
    elif profile.provider is Provider.ANTHROPIC:
        executable = shutil.which("claude") or "claude"
        env["CLAUDE_BASH_MAINTAIN_PROJECT_WORKING_DIR"] = "1"
        # A true value forces permissionMode=default even with the bypass flag.
        # Override an inherited value as well as the old IDEA default.
        env["CLAUDE_CODE_SUBPROCESS_ENV_SCRUB"] = "0"
        resume_args = ("--resume", resume_session_id) if resume_session_id else ()
        argv = (
            executable,
            "--print",
            "--model",
            profile.model,
            "--effort",
            profile.effort.value,
            "--dangerously-skip-permissions",
            "--setting-sources=",
            "--strict-mcp-config",
            "--mcp-config",
            _claude_mcp_config(env),
            "--tools",
            _CLAUDE_PEER_TOOLS,
            "--allowedTools",
            ",".join(f"mcp__idea__{name}" for name in _IDEA_MCP_TOOLS),
            "--settings",
            _claude_execution_settings(),
            "--append-system-prompt",
            system_prompt,
            "--output-format",
            "stream-json",
            "--verbose",
            *resume_args,
            task_prompt,
        )
    else:  # pragma: no cover - guarded by the enum
        raise ValueError(f"unsupported provider: {profile.provider}")
    return Invocation(argv=argv, cwd=workspace, env=env)


_SESSION_ID_PATTERN = re.compile(
    rb'"(?:thread_id|session_id)"\s*:\s*"([^"\\]{1,512})"'
)
_FINAL_REFUSAL_PATTERN = re.compile(
    rb'model_refusal_no_fallback|"stop_reason"\s*:\s*"refusal"',
    re.IGNORECASE,
)


def has_final_safeguard_refusal(path: Path, tail_bytes: int = 2 * 1024 * 1024) -> bool:
    """Recognize refusals written by launchers predating ProcessState.BLOCKED."""

    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - tail_bytes))
            tail = handle.read()
    except FileNotFoundError:
        return False
    return bool(_FINAL_REFUSAL_PATTERN.search(tail))


_PROCESS_STOP_GRACE = 3.0


async def _settle(task: asyncio.Future[Any]) -> Any:
    """Finish a spawn/cleanup even if shutdown is requested more than once."""
    while True:
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            if task.done():
                return task.result()


async def _stop_process_tree(
    process: asyncio.subprocess.Process, grace: float | None = None
) -> None:
    """Reap the trusted host and its group; grace applies only to shutdown."""
    grace = _PROCESS_STOP_GRACE if grace is None else grace
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    deadline = asyncio.get_running_loop().time() + grace
    # Process.wait() can wait for inherited stdout pipes even after direct exit.
    # returncode is set by the child watcher independently of those pipes.
    while process.returncode is None and asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(min(0.02, max(0, deadline - asyncio.get_running_loop().time())))
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    await process.wait()


async def _watch_process_host(process: asyncio.subprocess.Process) -> None:
    while process.returncode is None:
        await asyncio.sleep(0.02)
    # Also handle unexpected host death: an inherited stdout descriptor must
    # never keep the parent reader waiting after the lock holder has died.
    await _stop_process_tree(process, grace=0)


def _process_result(path: Path, host_code: int) -> int:
    try:
        with path.open("rb") as handle:
            raw = handle.read(1025)
        if len(raw) > 1024:
            return 1
        result = json.loads(raw)
        code = result.get("exit_code") if isinstance(result, dict) else None
        if isinstance(code, int) and not isinstance(code, bool) and -255 <= code <= 255:
            return code
    except (OSError, ValueError):
        pass
    # The supervisor normally kills itself after publishing the real CLI code.
    # Missing or invalid results are failures, never successful delivery acks.
    return host_code if host_code != 0 else 1


async def run_agent(
    *,
    forum: Forum,
    run_id: str,
    agent: dict[str, Any],
    profile: AgentProfile,
    invocation: Invocation,
    log_dir: Path,
) -> int:
    """Run one peer until its CLI exits; intentionally no wall-clock timeout."""

    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{profile.name}.jsonl"
    result_path = log_dir / f".execution-{uuid.uuid4().hex}.json"
    spawn = asyncio.create_task(asyncio.create_subprocess_exec(
        sys.executable, "-I", str(Path(__file__).with_name("process_host.py").resolve()),
        str(result_path.resolve()), "--", *invocation.argv,
        cwd=invocation.cwd,
        env=invocation.env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        start_new_session=True,
        pass_fds=invocation.inherited_fds,
    ))
    try:
        process = await asyncio.shield(spawn)
    except asyncio.CancelledError:
        try:
            process = await _settle(spawn)
        except (OSError, ValueError):
            process = None
        if process is not None:
            await _settle(asyncio.create_task(_stop_process_tree(process)))
        current = forum.get_agent(agent["id"])
        if current["process_state"] != ProcessState.RETIRED.value:
            forum.set_process_state(agent["id"], ProcessState.FAILED, exit_code=130)
        result_path.unlink(missing_ok=True)
        result_path.with_suffix(".tmp").unlink(missing_ok=True)
        raise
    except (FileNotFoundError, OSError) as error:
        forum.set_process_state(agent["id"], ProcessState.FAILED, exit_code=127)
        with log_path.open("a", encoding="utf-8") as log:
            log.write(f"launcher error: {error}\n")
        return 127

    assert process.stdout is not None
    watcher = asyncio.create_task(_watch_process_host(process))
    try:
        forum.set_process_state(agent["id"], ProcessState.RUNNING, pid=process.pid)
        with log_path.open("ab") as log:
            # StreamReader's line iterator has a 64 KiB separator limit. Model JSONL
            # events can legitimately put a much larger tool result on one line, so
            # preserve raw output in fixed-size chunks and scan only for the small
            # session-id field. Output size never determines whether the peer lives.
            scan_tail = b""
            refusal_scan_tail = b""
            provider_blocked = False
            session_known = bool(agent.get("session_id"))
            while chunk := await process.stdout.read(64 * 1024):
                log.write(chunk)
                log.flush()
                if profile.provider is Provider.ANTHROPIC and not provider_blocked:
                    refusal_scan = refusal_scan_tail + chunk
                    provider_blocked = bool(_FINAL_REFUSAL_PATTERN.search(refusal_scan))
                    refusal_scan_tail = refusal_scan[-1024:]
                if not session_known:
                    scan = scan_tail + chunk
                    match = _SESSION_ID_PATTERN.search(scan)
                    if match:
                        session_id = match.group(1).decode("utf-8", errors="replace")
                        forum.set_process_state(
                            agent["id"], ProcessState.RUNNING, session_id=session_id
                        )
                        session_known = True
                    else:
                        scan_tail = scan[-1024:]
        await watcher
        exit_code = _process_result(result_path, await process.wait())
    except asyncio.CancelledError:
        watcher.cancel()
        await _settle(asyncio.gather(watcher, return_exceptions=True))
        await _settle(asyncio.create_task(_stop_process_tree(process)))
        current = forum.get_agent(agent["id"])
        if current["process_state"] != ProcessState.RETIRED.value:
            forum.set_process_state(
                agent["id"], ProcessState.FAILED, exit_code=process.returncode or 130
            )
        raise
    except Exception:
        watcher.cancel()
        await _settle(asyncio.gather(watcher, return_exceptions=True))
        await _settle(asyncio.create_task(_stop_process_tree(process)))
        forum.set_process_state(agent["id"], ProcessState.FAILED, exit_code=1)
        raise
    finally:
        result_path.unlink(missing_ok=True)
        result_path.with_suffix(".tmp").unlink(missing_ok=True)
    current = forum.get_agent(agent["id"])
    if current["process_state"] != ProcessState.RETIRED.value:
        if provider_blocked:
            final_state = ProcessState.BLOCKED
        else:
            final_state = ProcessState.DORMANT if exit_code == 0 else ProcessState.FAILED
        forum.set_process_state(agent["id"], final_state, exit_code=exit_code)
    return exit_code
