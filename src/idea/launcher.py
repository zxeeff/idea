from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable, Iterable

from .domain import AgentProfile, Effort, ProcessState, Provider
from .forum import Forum
from .prompts import blocked_restart_task, resume_task, shared_prompt, user_task, wake_task
from .providers import (
    Invocation,
    build_invocation,
    log_reports_final_safeguard_refusal,
    run_agent,
)


@dataclass(frozen=True, slots=True)
class PreparedPeer:
    profile: AgentProfile
    agent: dict[str, object]
    invocation: Invocation


@dataclass(frozen=True, slots=True)
class PreparedRun:
    run: dict[str, object]
    peers: tuple[PreparedPeer, ...]


_LEGACY_DEFAULT_HANDLES = {
    "luna-1": "luna-medium",
    "terra-1": "terra-medium",
    "terra-2": "terra-high",
    "sol-1": "sol-high",
    "sol-2": "sol-xhigh",
    "sol-3": "sol-max",
    "daybreak-blue-1": "daybreak-ultra",
    "daybreak-blue-2": "daybreak-max",
    "sonnet-1": "sonnet-medium",
    "sonnet-2": "sonnet-high",
    "opus-1": "opus-high",
    "opus-2": "opus-high-2",
    "opus-3": "opus-xhigh",
    "opus-4": "opus-xhigh-2",
    "opus-5": "opus-max",
    "opus-6": "opus-max-2",
}


def _profile_signature(profile: AgentProfile) -> tuple[str, str, str]:
    return (profile.provider.value, profile.model, profile.effort.value)


def _record_signature(record: dict[str, object]) -> tuple[str, str, str]:
    return (
        str(record["provider"]),
        str(record["model"]),
        str(record["effort"]),
    )


def prepare_run(
    *,
    forum: Forum,
    goal: str,
    workspace: Path,
    profiles: Iterable[AgentProfile],
) -> PreparedRun:
    workspace = workspace.expanduser().resolve()
    if not workspace.is_dir():
        raise NotADirectoryError(f"workspace is not a directory: {workspace}")
    profiles = tuple(profiles)
    run = forum.create_run(goal, workspace)
    forum.create_thread(
        str(run["id"]),
        "user",
        "Objective",
        goal,
    )
    peers: list[PreparedPeer] = []
    for profile in profiles:
        agent = forum.register_agent(str(run["id"]), profile)
        system_prompt = shared_prompt(
            name=profile.name,
            peer_names=(peer.name for peer in profiles),
        )
        invocation = build_invocation(
            profile=profile,
            system_prompt=system_prompt,
            task_prompt=user_task(goal),
            workspace=workspace,
            state_dir=forum.state_dir,
            run_id=str(run["id"]),
            agent=agent,
        )
        peers.append(PreparedPeer(profile=profile, agent=agent, invocation=invocation))
    return PreparedRun(run=run, peers=tuple(peers))


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


def prepare_resume(
    *,
    forum: Forum,
    run_id: str,
    profile_names: Iterable[str] | None = None,
    fresh_sessions: bool = False,
    reset_processes: bool = True,
    additional_profiles: Iterable[AgentProfile] = (),
) -> PreparedRun:
    """Re-enter an interrupted run without creating a planner or a new forum."""

    run = forum.get_run(run_id)
    workspace = Path(str(run["workspace"])).expanduser().resolve()
    records = forum.list_agents(run_id)
    additional_profiles = tuple(additional_profiles)
    known_by_name = {str(record["name"]): record for record in records}

    # Keep persisted handles stable while recognizing the default-handle rename
    # during --expand-defaults. A signature check prevents an unrelated custom
    # profile that reused an old name from being mistaken for the old default.
    aliases: dict[str, str] = {}
    known_names = set(known_by_name)
    for profile in additional_profiles:
        record = known_by_name.get(profile.name)
        if record is not None:
            if _record_signature(record) != _profile_signature(profile):
                raise ValueError(
                    f"profile {profile.name!r} already exists with different execution settings"
                )
            continue

        legacy_name = _LEGACY_DEFAULT_HANDLES.get(profile.name)
        legacy_record = known_by_name.get(legacy_name) if legacy_name else None
        if (
            legacy_record is not None
            and _record_signature(legacy_record) == _profile_signature(profile)
        ):
            aliases[profile.name] = str(legacy_record["name"])
            continue
        if profile.name in known_names:
            raise ValueError(f"duplicate additional profile name: {profile.name!r}")
        forum.register_agent(run_id, profile)
        known_names.add(profile.name)
    records = forum.list_agents(run_id)
    requested = set(profile_names or ())
    known = {str(record["name"]) for record in records}
    missing = requested - known - set(aliases)
    if missing:
        raise ValueError(f"unknown profiles in run: {', '.join(sorted(missing))}")
    wanted = {aliases.get(name, name) for name in requested}
    all_profiles = tuple(
        AgentProfile(
            name=str(record["name"]),
            provider=Provider(str(record["provider"])),
            model=str(record["model"]),
            effort=Effort(str(record["effort"])),
        )
        for record in records
    )
    peers: list[PreparedPeer] = []
    for record, profile in zip(records, all_profiles, strict=True):
        if wanted and profile.name not in wanted:
            continue
        if record["process_state"] == ProcessState.RETIRED.value:
            continue
        if record["process_state"] == ProcessState.RUNNING.value and _pid_is_alive(record["pid"]):
            continue
        was_blocked = record["process_state"] == ProcessState.BLOCKED.value
        if (
            not was_blocked
            and record["process_state"] == ProcessState.FAILED.value
            and profile.provider is Provider.ANTHROPIC
        ):
            was_blocked = log_reports_final_safeguard_refusal(
                forum.state_dir / "runs" / run_id / "logs" / f"{profile.name}.jsonl"
            )
        restart_fresh = fresh_sessions or was_blocked
        session_id = None if restart_fresh else record.get("session_id")
        if reset_processes:
            forum.reset_process_observation(str(record["id"]), clear_session=restart_fresh)
        if restart_fresh:
            record["session_id"] = None
        system_prompt = shared_prompt(
            name=profile.name,
            peer_names=(peer.name for peer in all_profiles),
        )
        invocation = build_invocation(
            profile=profile,
            system_prompt=system_prompt,
            task_prompt=(
                blocked_restart_task(str(run["goal"]))
                if was_blocked
                else resume_task(str(run["goal"]))
            ),
            workspace=workspace,
            state_dir=forum.state_dir,
            run_id=run_id,
            agent=record,
            resume_session_id=str(session_id) if session_id else None,
        )
        peers.append(PreparedPeer(profile=profile, agent=record, invocation=invocation))
    if not peers:
        raise RuntimeError("no stopped peer is available to resume")
    return PreparedRun(run=run, peers=tuple(peers))


async def launch_all(
    *,
    forum: Forum,
    prepared: PreparedRun,
    on_started: Callable[[PreparedRun], None] | None = None,
) -> list[int]:
    """Start every peer at once and wait; there are no phases or agent deadlines."""

    if on_started:
        on_started(prepared)
    run_id = str(prepared.run["id"])
    log_dir = forum.state_dir / "runs" / run_id / "logs"
    tasks = [
        asyncio.create_task(
            run_agent(
                forum=forum,
                run_id=run_id,
                agent=peer.agent,
                profile=peer.profile,
                invocation=peer.invocation,
                log_dir=log_dir,
            ),
            name=peer.profile.name,
        )
        for peer in prepared.peers
    ]
    return list(await asyncio.gather(*tasks))


async def run_reactor(
    *,
    forum: Forum,
    prepared: PreparedRun,
    on_started: Callable[[PreparedRun], None] | None = None,
    runner: Callable[..., Awaitable[int]] = run_agent,
    poll_interval: float = 0.5,
) -> list[int]:
    """Keep peers resident and wake dormant sessions from forum activity.

    The polling interval only observes the SQLite event log. It never limits or
    interrupts an agent process.
    """

    if on_started:
        on_started(prepared)
    run_id = str(prepared.run["id"])
    goal = str(prepared.run["goal"])
    workspace = Path(str(prepared.run["workspace"])).expanduser().resolve()
    log_dir = forum.state_dir / "runs" / run_id / "logs"
    profiles = tuple(peer.profile for peer in prepared.peers)
    peers = {str(peer.agent["id"]): peer for peer in prepared.peers}
    active: dict[str, asyncio.Task[int]] = {}
    latest_codes: dict[str, int] = {}
    last_scanned_activity = -1

    def start(agent_id: str, invocation: Invocation) -> None:
        peer = peers[agent_id]
        active[agent_id] = asyncio.create_task(
            runner(
                forum=forum,
                run_id=run_id,
                agent=peer.agent,
                profile=peer.profile,
                invocation=invocation,
                log_dir=log_dir,
            ),
            name=peer.profile.name,
        )

    for agent_id, peer in peers.items():
        start(agent_id, peer.invocation)

    try:
        while True:
            if active:
                done, _ = await asyncio.wait(
                    set(active.values()),
                    timeout=poll_interval,
                    return_when=asyncio.FIRST_COMPLETED,
                )
            else:
                await asyncio.sleep(poll_interval)
                done = set()

            for agent_id, task in tuple(active.items()):
                if task not in done:
                    continue
                try:
                    latest_codes[agent_id] = task.result()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    forum.set_process_state(agent_id, ProcessState.FAILED, exit_code=1)
                    latest_codes[agent_id] = 1
                del active[agent_id]

            current_activity = forum.activity_high_water(run_id)
            if not done and current_activity == last_scanned_activity:
                continue
            last_scanned_activity = current_activity
            records = {str(item["id"]): item for item in forum.list_agents(run_id)}
            if peers and all(
                records[agent_id]["process_state"] == ProcessState.RETIRED.value
                for agent_id in peers
            ):
                return [latest_codes.get(str(peer.agent["id"]), 0) for peer in prepared.peers]

            for agent_id, peer in peers.items():
                if agent_id in active:
                    continue
                record = records[agent_id]
                if record["process_state"] == ProcessState.RETIRED.value:
                    continue
                if (
                    record["process_state"] == ProcessState.RUNNING.value
                    and _pid_is_alive(record["pid"])
                ):
                    continue
                triggers, scan_high_water = forum.wake_events(agent_id)
                forum.mark_wake_scanned(agent_id, scan_high_water)
                if not triggers:
                    continue

                activity, high_water = forum.unseen_activity(agent_id)
                forum.mark_activity_seen(agent_id, high_water)
                trigger_ids = {int(item["id"]) for item in triggers}
                background = [
                    item for item in activity if int(item["id"]) not in trigger_ids
                ]
                user_triggers = [
                    item
                    for item in triggers
                    if str(item["author"]).casefold() in {"human", "user"}
                ]
                primary_trigger = (user_triggers or triggers)[-1]
                was_blocked = record["process_state"] == ProcessState.BLOCKED.value
                forum.reset_process_observation(agent_id, clear_session=was_blocked)
                record = forum.get_agent(agent_id)
                system_prompt = shared_prompt(
                    name=peer.profile.name,
                    peer_names=(profile.name for profile in profiles),
                )
                invocation = build_invocation(
                    profile=peer.profile,
                    system_prompt=system_prompt,
                    task_prompt=(
                        blocked_restart_task(goal, triggers, background)
                        if was_blocked
                        else wake_task(goal, triggers, background)
                    ),
                    workspace=workspace,
                    state_dir=forum.state_dir,
                    run_id=run_id,
                    agent=record,
                    resume_session_id=(
                        None
                        if was_blocked
                        else (str(record["session_id"]) if record.get("session_id") else None)
                    ),
                    trigger_event_id=int(primary_trigger["id"]),
                    trigger_thread_id=(
                        str(primary_trigger["thread_id"])
                        if primary_trigger.get("thread_id")
                        else None
                    ),
                )
                peer.agent.update(record)
                start(agent_id, invocation)
    finally:
        remaining = tuple(active.values())
        for task in remaining:
            task.cancel()
        if remaining:
            await asyncio.gather(*remaining, return_exceptions=True)
