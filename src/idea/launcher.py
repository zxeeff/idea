from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Awaitable, Callable, Iterable

from .domain import AgentProfile, Effort, ProcessState, Provider
from .execution import RunLock
from .forum import Forum
from .prompts import blocked_restart_task, resume_task, shared_prompt, user_task
from .providers import Invocation, build_invocation, has_final_safeguard_refusal, run_agent, validate_workspace_boundary


@dataclass(frozen=True, slots=True)
class PreparedPeer:
    profile: AgentProfile
    agent: dict[str, object]
    invocation: Invocation


@dataclass(frozen=True, slots=True)
class PreparedRun:
    run: dict[str, object]
    peers: tuple[PreparedPeer, ...]


def prepare_run(*, forum: Forum, goal: str, workspace: Path, profiles: Iterable[AgentProfile]) -> PreparedRun:
    workspace = workspace.expanduser().resolve()
    if not workspace.is_dir():
        raise NotADirectoryError(f"workspace is not a directory: {workspace}")
    validate_workspace_boundary(workspace=workspace, state_dir=forum.state_dir)
    profiles = tuple(profiles)
    run = forum.create_run(goal, workspace)
    forum.create_thread(str(run["id"]), "user", "Objective", goal)
    peers = []
    for profile in profiles:
        agent = forum.register_agent(str(run["id"]), profile)
        peers.append(PreparedPeer(profile, agent, build_invocation(
            profile=profile,
            system_prompt=shared_prompt(name=profile.name, peer_names=()),
            task_prompt=user_task(goal),
            workspace=workspace,
            state_dir=forum.state_dir,
            run_id=str(run["id"]),
            agent=agent,
        )))
    return PreparedRun(run, tuple(peers))


def _pid_is_alive(pid: object) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _prepare_resume(*, forum: Forum, run_id: str, profile_names: Iterable[str] | None = None,
                    fresh_sessions: bool = False, reset_processes: bool = True) -> PreparedRun:
    run = forum.get_run(run_id)
    workspace = Path(str(run["workspace"])).expanduser().resolve()
    validate_workspace_boundary(workspace=workspace, state_dir=forum.state_dir)
    records = forum.list_agents(run_id)
    wanted = set(profile_names or ())
    missing = wanted - {str(record["name"]) for record in records}
    if missing:
        raise ValueError(f"unknown profiles in run: {', '.join(sorted(missing))}")
    profiles = tuple(AgentProfile(str(record["name"]), Provider(str(record["provider"])),
                                  str(record["model"]), Effort(str(record["effort"]))) for record in records)
    peers = []
    for record, profile in zip(records, profiles, strict=True):
        if (wanted and profile.name not in wanted
                or record["process_state"] == ProcessState.RETIRED.value
                or record["process_state"] == ProcessState.RUNNING.value and _pid_is_alive(record["pid"])):
            continue
        was_blocked = record["process_state"] == ProcessState.BLOCKED.value
        if not was_blocked and record["process_state"] == ProcessState.FAILED.value and profile.provider is Provider.ANTHROPIC:
            was_blocked = has_final_safeguard_refusal(
                forum.state_dir / "runs" / run_id / "logs" / f"{profile.name}.jsonl"
            )
        restart_fresh = fresh_sessions or was_blocked
        session_id = None if restart_fresh else record.get("session_id")
        if reset_processes and not forum.reset_process_observation(str(record["id"]), clear_session=restart_fresh):
            continue
        if restart_fresh:
            record["session_id"] = None
        peers.append(PreparedPeer(profile, record, build_invocation(
            profile=profile,
            system_prompt=shared_prompt(name=profile.name, peer_names=()),
            task_prompt=blocked_restart_task(str(run["goal"])) if was_blocked else resume_task(str(run["goal"])),
            workspace=workspace,
            state_dir=forum.state_dir,
            run_id=run_id,
            agent=record,
            resume_session_id=str(session_id) if session_id else None,
        )))
    if not peers:
        raise RuntimeError("no stopped peer is available to resume")
    return PreparedRun(run, tuple(peers))


async def _run_fixed_reactor(*, forum: Forum, prepared: PreparedRun,
                             on_started: Callable[[PreparedRun], None] | None,
                             runner: Callable[..., Awaitable[int]], poll_interval: float,
                             lock_fd: int) -> list[int]:
    """Run the fixed initial peer set once; the board never schedules extra turns."""

    run_id = str(prepared.run["id"])
    log_dir = forum.state_dir / "runs" / run_id / "logs"
    peers = {str(peer.agent["id"]): peer for peer in prepared.peers}
    active: dict[str, asyncio.Task[int]] = {}
    latest_codes: dict[str, int] = {}

    def start(peer: PreparedPeer) -> None:
        active[str(peer.agent["id"])] = asyncio.create_task(runner(
            forum=forum,
            run_id=run_id,
            agent=peer.agent,
            profile=peer.profile,
            invocation=replace(peer.invocation, inherited_fds=(lock_fd,)),
            log_dir=log_dir,
        ), name=peer.profile.name)

    for peer in peers.values():
        start(peer)
    if on_started:
        on_started(prepared)
    try:
        while active:
            await asyncio.wait(tuple(active.values()), timeout=poll_interval, return_when=asyncio.FIRST_COMPLETED)
            for agent_id, task in tuple(active.items()):
                if not task.done():
                    continue
                try:
                    latest_codes[agent_id] = task.result()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    latest_codes[agent_id] = 1
                    forum.set_process_state(agent_id, ProcessState.FAILED, exit_code=1)
                del active[agent_id]
        return [latest_codes.get(agent_id, 0) for agent_id in peers]
    finally:
        for task in active.values():
            task.cancel()
        if active:
            await asyncio.gather(*active.values(), return_exceptions=True)


async def run_reactor(*, forum: Forum, prepared: PreparedRun,
                      on_started: Callable[[PreparedRun], None] | None = None,
                      runner: Callable[..., Awaitable[int]] = run_agent,
                      poll_interval: float = 0.5, run_lock: RunLock | None = None) -> list[int]:
    if poll_interval <= 0:
        raise ValueError("poll_interval must be positive")
    run_id = str(prepared.run["id"])
    if run_lock is not None:
        if run_lock.fd is None or run_lock.path != RunLock(forum.state_dir, run_id).path:
            raise ValueError("run_lock must be held for this run")
        return await _run_fixed_reactor(forum=forum, prepared=prepared, on_started=on_started,
                                        runner=runner, poll_interval=poll_interval, lock_fd=run_lock.fd)
    with RunLock(forum.state_dir, run_id) as lock:
        assert lock.fd is not None
        return await _run_fixed_reactor(forum=forum, prepared=prepared, on_started=on_started,
                                        runner=runner, poll_interval=poll_interval, lock_fd=lock.fd)


def prepare_resume(*, forum: Forum, run_id: str, profile_names: Iterable[str] | None = None,
                   fresh_sessions: bool = False, reset_processes: bool = True,
                   run_lock: RunLock | None = None) -> PreparedRun:
    if run_lock is not None:
        if run_lock.fd is None or run_lock.path != RunLock(forum.state_dir, run_id).path:
            raise ValueError("run_lock must be held for this run")
        return _prepare_resume(forum=forum, run_id=run_id, profile_names=profile_names,
                               fresh_sessions=fresh_sessions, reset_processes=reset_processes)
    with RunLock(forum.state_dir, run_id):
        return _prepare_resume(forum=forum, run_id=run_id, profile_names=profile_names,
                               fresh_sessions=fresh_sessions, reset_processes=reset_processes)
