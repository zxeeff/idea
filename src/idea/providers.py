from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .domain import AgentProfile, ProcessState, Provider
from .forum import Forum


@dataclass(frozen=True, slots=True)
class Invocation:
    argv: tuple[str, ...]
    cwd: Path
    env: dict[str, str]


def _module_root() -> Path:
    return Path(__file__).resolve().parents[1]


def agent_environment(
    *,
    state_dir: Path,
    run_id: str,
    agent: dict[str, Any],
    trigger_event_id: int | None = None,
    trigger_thread_id: str | None = None,
) -> dict[str, str]:
    env = os.environ.copy()
    # The browser password belongs to the launcher process. Never expose it to
    # autonomous provider subprocesses, especially in bypass-permission mode.
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
    # Each provider turn is a new OS process. Remove inherited routing hints so
    # an ordinary start/resume can never accidentally reply to an older trigger.
    env.pop("IDEA_TRIGGER_EVENT_ID", None)
    env.pop("IDEA_TRIGGER_THREAD_ID", None)
    if trigger_event_id is not None:
        env["IDEA_TRIGGER_EVENT_ID"] = str(trigger_event_id)
    if trigger_thread_id:
        env["IDEA_TRIGGER_THREAD_ID"] = str(trigger_thread_id)
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
    trigger_event_id: int | None = None,
    trigger_thread_id: str | None = None,
) -> Invocation:
    env = agent_environment(
        state_dir=state_dir,
        run_id=run_id,
        agent=agent,
        trigger_event_id=trigger_event_id,
        trigger_thread_id=trigger_thread_id,
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
        )
        if resume_session_id:
            argv = (
                executable,
                "exec",
                "resume",
                *common,
                "--dangerously-bypass-approvals-and-sandbox",
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
                "--dangerously-bypass-approvals-and-sandbox",
                "--cd",
                str(workspace),
                "--skip-git-repo-check",
                "--json",
                task_prompt,
            )
    elif profile.provider is Provider.ANTHROPIC:
        executable = shutil.which("claude") or "claude"
        resume_args = ("--resume", resume_session_id) if resume_session_id else ()
        argv = (
            executable,
            "--print",
            "--model",
            profile.model,
            "--effort",
            profile.effort.value,
            "--dangerously-skip-permissions",
            "--add-dir",
            str(state_dir),
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


def log_reports_final_safeguard_refusal(path: Path, tail_bytes: int = 2 * 1024 * 1024) -> bool:
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
    try:
        process = await asyncio.create_subprocess_exec(
            *invocation.argv,
            cwd=invocation.cwd,
            env=invocation.env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    except (FileNotFoundError, OSError) as error:
        forum.set_process_state(agent["id"], ProcessState.FAILED, exit_code=127)
        log_path.write_text(f"launcher error: {error}\n", encoding="utf-8")
        return 127

    forum.set_process_state(agent["id"], ProcessState.RUNNING, pid=process.pid)
    assert process.stdout is not None
    try:
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
        exit_code = await process.wait()
    except asyncio.CancelledError:
        if process.returncode is None:
            process.terminate()
            await process.wait()
        current = forum.get_agent(agent["id"])
        if current["process_state"] != ProcessState.RETIRED.value:
            forum.set_process_state(
                agent["id"], ProcessState.FAILED, exit_code=process.returncode or 130
            )
        raise
    current = forum.get_agent(agent["id"])
    if current["process_state"] != ProcessState.RETIRED.value:
        if provider_blocked:
            final_state = ProcessState.BLOCKED
        else:
            final_state = ProcessState.DORMANT if exit_code == 0 else ProcessState.FAILED
        forum.set_process_state(agent["id"], final_state, exit_code=exit_code)
    return exit_code
