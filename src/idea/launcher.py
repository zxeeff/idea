from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Awaitable, Callable, Iterable

from .domain import AgentProfile, Effort, ProcessState, Provider
from .communication import CommunicationStore
from .execution import ExecutionStore, RunLock
from .forum import Forum
from .prompts import (
    blocked_restart_task, resume_task, select_wake_context, shared_prompt, user_task, wake_task,
)
from .providers import (
    Invocation,
    build_invocation,
    log_reports_final_safeguard_refusal,
    _settle,
    run_agent,
    validate_workspace_boundary,
)


@dataclass(frozen=True, slots=True)
class PreparedPeer:
    profile: AgentProfile
    agent: dict[str, object]
    invocation: Invocation
    start_requested: bool = False


@dataclass(frozen=True, slots=True)
class PreparedRun:
    run: dict[str, object]
    peers: tuple[PreparedPeer, ...]
    allow_join: bool = True


def _delivery_ids(events: Iterable[dict]) -> list[int]:
    """Expand only selected notices into the original delivery journal IDs."""
    return sorted({
        int(identifier)
        for event in events
        for identifier in event.get("_delivery_event_ids", [event["id"]])
    })


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


def _peer_workspace(forum: Forum, run_id: str, agent_id: str, original: Path) -> tuple[Path, Path | None]:
    from .workspaces import WorkspaceStore

    store = WorkspaceStore(forum, run_id)
    if not store.configured():
        return original, None
    workspace = store.prepare(agent_id)
    isolated = store.summary()["mode"] == "isolated"
    return workspace, workspace / ".idea-peer" if isolated else None


def _registered_peer(forum: Forum, run: dict[str, object], record: dict[str, object], *, task: str | None = None) -> PreparedPeer:
    profile = AgentProfile(name=str(record["name"]), provider=Provider(str(record["provider"])),
                           model=str(record["model"]), effort=Effort(str(record["effort"])))
    workspace, bridge_dir = _peer_workspace(forum, str(run["id"]), str(record["id"]), Path(str(run["workspace"])))
    if task is None:
        from .population import PopulationStore

        birth = PopulationStore(forum, str(run["id"])).birth_for_agent(str(record["id"]))
        task = user_task(str(run["goal"]))
        if birth and birth.get("call_id"):
            invitation = {key: birth.get(key) for key in ("call_id", "thread_id", "reason")}
            task = ("A public invitation introduced you to this discussion. Read its evidence and "
                    "choose your own useful contribution; the inviter does not assign your role.\n"
                    + json.dumps(invitation, ensure_ascii=False) + "\n\n" + task)
    invocation = build_invocation(
        profile=profile, system_prompt=shared_prompt(name=profile.name, peer_names=()),
        task_prompt=task, workspace=workspace, state_dir=forum.state_dir, run_id=str(run["id"]),
        agent=record, resume_session_id=str(record["session_id"]) if record.get("session_id") else None,
        bridge_dir=bridge_dir,
    )
    return PreparedPeer(profile=profile, agent=record, invocation=invocation)


def prepare_run(
    *,
    forum: Forum,
    goal: str,
    workspace: Path,
    profiles: Iterable[AgentProfile],
    population_policy=None,
    adaptive: bool = False,
    workspace_mode: str | None = None,
) -> PreparedRun:
    workspace = workspace.expanduser().resolve()
    if not workspace.is_dir():
        raise NotADirectoryError(f"workspace is not a directory: {workspace}")
    validate_workspace_boundary(workspace=workspace, state_dir=forum.state_dir)
    profiles = tuple(profiles)
    run = forum.create_run(goal, workspace)
    forum.create_thread(
        str(run["id"]),
        "user",
        "Objective",
        goal,
    )
    if workspace_mode is not None:
        from .workspaces import WorkspaceStore

        WorkspaceStore(forum, str(run["id"])).configure(mode=workspace_mode)
    if population_policy is not None and adaptive:
        from .population import PopulationStore

        population = PopulationStore(forum, str(run["id"]))
        population.configure(profiles=profiles, policy=population_policy, enabled=True)
        peers = []
        while birth := population.reserve_birth(initial=True):
            try:
                peers.append(_registered_peer(forum, run, birth["agent"]))
                population.finish_birth(birth["birth_id"], succeeded=True)
            except Exception as error:
                population.finish_birth(birth["birth_id"], succeeded=False, error=str(error))
                raise
        return PreparedRun(run=run, peers=tuple(peers))
    peers: list[PreparedPeer] = []
    for profile in profiles:
        agent = forum.register_agent(str(run["id"]), profile)
        peer_workspace, bridge_dir = _peer_workspace(forum, str(run["id"]), str(agent["id"]), workspace)
        system_prompt = shared_prompt(
            name=profile.name,
            peer_names=(peer.name for peer in profiles),
        )
        invocation = build_invocation(
            profile=profile,
            system_prompt=system_prompt,
            task_prompt=user_task(goal),
            workspace=peer_workspace,
            state_dir=forum.state_dir,
            run_id=str(run["id"]),
            agent=agent,
            bridge_dir=bridge_dir,
        )
        peers.append(PreparedPeer(profile=profile, agent=agent, invocation=invocation))
    if population_policy is not None:
        from .population import PopulationStore

        PopulationStore(forum, str(run["id"])).configure(profiles=profiles, policy=population_policy, enabled=False)
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


def _has_owned_attempt(forum: Forum, agent_id: str) -> bool:
    """Updated launchers fence providers with RunLock; legacy PIDs still need care."""
    with forum._connection() as connection:
        if connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='execution_attempts'").fetchone() is None:
            return False
        return connection.execute(
            """SELECT 1 FROM execution_attempts t JOIN execution_requests r ON r.id=t.request_id
               WHERE r.agent_id=? LIMIT 1""", (agent_id,),
        ).fetchone() is not None


def _prepare_resume(
    *,
    forum: Forum,
    run_id: str,
    profile_names: Iterable[str] | None = None,
    fresh_sessions: bool = False,
    reset_processes: bool = True,
    additional_profiles: Iterable[AgentProfile] = (),
    workspace_mode: str | None = None,
) -> PreparedRun:
    """Re-enter an interrupted run without creating a planner or a new forum."""

    run = forum.get_run(run_id)
    workspace = Path(str(run["workspace"])).expanduser().resolve()
    validate_workspace_boundary(workspace=workspace, state_dir=forum.state_dir)
    if workspace_mode is not None:
        from .workspaces import WorkspaceStore

        copies = WorkspaceStore(forum, run_id)
        previously_isolated = copies.configured() and copies.summary()["mode"] == "isolated"
        copies.configure(mode=workspace_mode)
        if workspace_mode == "isolated" and not previously_isolated:
            fresh_sessions = True
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
        from .population import PopulationStore

        population = PopulationStore(forum, run_id)
        if population.configured() and population.summary()["live_agents"] >= population.policy().max_agents:
            raise ValueError("the persisted population limit does not allow more profiles")
        forum.register_agent(run_id, profile)
        known_names.add(profile.name)
    records = forum.list_agents(run_id)
    from .population import PopulationStore

    population = PopulationStore(forum, run_id)
    if population.configured():
        population.configure()
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
        if (record["process_state"] == ProcessState.RUNNING.value and _pid_is_alive(record["pid"])
                and not _has_owned_attempt(forum, str(record["id"]))):
            continue
        peer_workspace, bridge_dir = _peer_workspace(forum, run_id, str(record["id"]), workspace)
        from .workspaces import WorkspaceStore

        copies = WorkspaceStore(forum, run_id)
        rebase_session = copies.fresh_session_required(str(record["id"])) if bridge_dir else False
        was_blocked = record["process_state"] == ProcessState.BLOCKED.value
        if (
            not was_blocked
            and record["process_state"] == ProcessState.FAILED.value
            and profile.provider is Provider.ANTHROPIC
        ):
            was_blocked = log_reports_final_safeguard_refusal(
                forum.state_dir / "runs" / run_id / "logs" / f"{profile.name}.jsonl"
            )
        keep_parked = record.get("participation_state") == "parked" and not (
            wanted or fresh_sessions or rebase_session
        )
        restart_fresh = fresh_sessions or rebase_session or (was_blocked and not keep_parked)
        session_id = None if restart_fresh else record.get("session_id")
        if reset_processes and not keep_parked:
            if not forum.reset_process_observation(str(record["id"]), clear_session=restart_fresh):
                continue
            if rebase_session:
                copies.acknowledge_session_reset(str(record["id"]))
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
            workspace=peer_workspace,
            state_dir=forum.state_dir,
            run_id=run_id,
            agent=record,
            resume_session_id=str(session_id) if session_id else None,
            bridge_dir=bridge_dir,
        )
        peers.append(PreparedPeer(
            profile=profile, agent=record, invocation=invocation,
            start_requested=bool(wanted or fresh_sessions or rebase_session),
        ))
    if not peers and not (population.configured() and population.summary()["enabled"] and not wanted):
        raise RuntimeError("no stopped peer is available to resume")
    return PreparedRun(run=run, peers=tuple(peers), allow_join=not wanted)


async def run_reactor(
    *,
    forum: Forum,
    prepared: PreparedRun,
    on_started: Callable[[PreparedRun], None] | None = None,
    runner: Callable[..., Awaitable[int]] = run_agent,
    poll_interval: float = 0.5,
    max_concurrent: int | None = None,
    max_codex: int | None = None,
    max_claude: int | None = None,
    run_lock: RunLock | None = None,
) -> list[int]:
    """Deliver notifications within shared process limits; peers choose their work."""
    if poll_interval <= 0:
        raise ValueError("poll_interval must be positive")
    run_id = str(prepared.run["id"])
    if run_lock is not None:
        if run_lock.fd is None or run_lock.path != RunLock(forum.state_dir, run_id).path:
            raise ValueError("run_lock must be held for this run")
        return await _run_reactor_locked(
            forum=forum, prepared=prepared, on_started=on_started, runner=runner,
            poll_interval=poll_interval, max_concurrent=max_concurrent,
            max_codex=max_codex, max_claude=max_claude, lock_fd=run_lock.fd,
        )
    with RunLock(forum.state_dir, run_id) as lock:
        assert lock.fd is not None
        return await _run_reactor_locked(
            forum=forum, prepared=prepared, on_started=on_started, runner=runner,
            poll_interval=poll_interval, max_concurrent=max_concurrent,
            max_codex=max_codex, max_claude=max_claude, lock_fd=lock.fd,
        )


async def _run_reactor_locked(
    *, forum: Forum, prepared: PreparedRun,
    on_started: Callable[[PreparedRun], None] | None,
    runner: Callable[..., Awaitable[int]], poll_interval: float,
    max_concurrent: int | None, max_codex: int | None, max_claude: int | None,
    lock_fd: int,
) -> list[int]:
    run_id = str(prepared.run["id"])
    goal = str(prepared.run["goal"])
    workspace = Path(str(prepared.run["workspace"])).expanduser().resolve()
    log_dir = forum.state_dir / "runs" / run_id / "logs"
    peers = {str(peer.agent["id"]): peer for peer in prepared.peers}
    from .population import PopulationStore
    from .bridge import BridgeServer
    from .commands import PEER_COMMANDS, dispatch_forum

    population = PopulationStore(forum, run_id)
    managed = population.configured()
    store = ExecutionStore(forum, run_id, population=population if managed else None)
    communication = CommunicationStore(forum, run_id)
    communication.configure()
    bridge = BridgeServer(
        forum, run_id,
        handler=lambda agent_id, command, payload: dispatch_forum(forum, run_id, agent_id, command, payload),
        allowed_commands=PEER_COMMANDS,
    )
    next_response_cleanup = 0.0

    def poll_bridge() -> int:
        nonlocal next_response_cleanup
        count = bridge.poll()
        if time.monotonic() >= next_response_cleanup:
            bridge.prune_responses(older_than=(datetime.now(UTC) - timedelta(days=1)).isoformat())
            next_response_cleanup = time.monotonic() + 60
        return count

    def connect_peer(peer: PreparedPeer) -> None:
        if peer.invocation.env.get("IDEA_BRIDGE_DIR"):
            bridge.register(str(peer.agent["id"]), peer.invocation.cwd)

    for peer in peers.values():
        connect_peer(peer)
    policy = store.configure(
        max_concurrent=max_concurrent, max_codex=max_codex, max_claude=max_claude
    )
    store.recover()
    active: dict[str, tuple[asyncio.Task[int], int, str]] = {}
    latest_codes: dict[str, int] = {}
    held: dict[str, int] = {}
    preparation: tuple[dict, asyncio.Task[PreparedPeer]] | None = None
    next_idle_cleanup = 0.0
    for agent_id in peers:
        if peers[agent_id].start_requested or forum.get_agent(agent_id).get("participation_state") != "parked":
            store.enqueue(agent_id, kind="start")
        else:
            held[agent_id] = store.held_through(agent_id)
    if on_started:
        on_started(prepared)

    def enqueue_notifications() -> dict[str, dict[str, object]]:
        records = {str(item["id"]): item for item in forum.list_agents(run_id)}
        queued_ids = {str(request["agent_id"]) for request in store.queued()}
        for item in forum.pending_notification_agents(run_id, limit=500):
            agent_id = str(item["agent_id"])
            if agent_id not in peers or agent_id in active or agent_id in queued_ids:
                continue
            state = records[agent_id]["process_state"]
            if state == ProcessState.RETIRED.value:
                continue
            if state in {ProcessState.BLOCKED.value, ProcessState.FAILED.value}:
                signal = int(item["last_explicit_event_id"])
            else:
                signal = int(item["last_event_id"])
            if signal <= held.get(agent_id, 0):
                continue
            store.enqueue(agent_id)
            queued_ids.add(agent_id)
        return records

    def invocation_for(
        peer: PreparedPeer, record: dict[str, object], kind: str,
        triggers: list[dict[str, object]], background: list[dict[str, object]],
        overflow: dict[str, object],
    ) -> Invocation:
        # Only explicit mentions carry reply-trigger routing. A followed thread
        # update is public context; it does not impersonate a direct request.
        mentions = [event for event in triggers
                    if event.get("notification_reason") in {"mention", "broadcast"}]
        human = [event for event in mentions
                 if str(event["author"]).casefold() in {"human", "user"}]
        activation = [event for event in mentions if event.get("activation_trigger") is True]
        primary = max(activation or human or mentions, key=lambda event: int(event["id"])) if mentions else None
        was_blocked = record["process_state"] == ProcessState.BLOCKED.value
        if triggers:
            task_prompt = (
                blocked_restart_task(goal, triggers, background, overflow=overflow)
                if was_blocked else wake_task(goal, triggers, background, overflow=overflow)
            )
        elif kind == "start":
            task_prompt = peer.invocation.argv[-1]
        else:
            task_prompt = resume_task(goal)
        session_id = None if was_blocked else record.get("session_id")
        invocation = build_invocation(
            profile=peer.profile,
            system_prompt=shared_prompt(name=peer.profile.name, peer_names=()),
            task_prompt=task_prompt, workspace=peer.invocation.cwd, state_dir=forum.state_dir,
            run_id=run_id, agent=record,
            resume_session_id=str(session_id) if session_id else None,
            trigger_event_id=int(primary["id"]) if primary else None,
            trigger_thread_id=str(primary["thread_id"]) if primary and primary.get("thread_id") else None,
            bridge_dir=Path(peer.invocation.env["IDEA_BRIDGE_DIR"]) if peer.invocation.env.get("IDEA_BRIDGE_DIR") else None,
        )
        return replace(invocation, inherited_fds=(lock_fd,))

    try:
        while True:
            # Join the bounded mailbox worker even if cancellation arrives, so
            # no forum write outlives this run's owner lock.
            poll = asyncio.create_task(asyncio.to_thread(poll_bridge))
            try:
                await asyncio.shield(poll)
            except asyncio.CancelledError:
                await _settle(poll)
                raise
            # Capture completions before admitting another process. RETIRED in
            # the forum is a peer decision, not proof its process has exited.
            for agent_id, (task, request_id, attempt_id) in tuple(active.items()):
                if not task.done():
                    continue
                try:
                    code = task.result()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    code = 1
                    forum.set_process_state(agent_id, ProcessState.FAILED, exit_code=1)
                latest_codes[agent_id] = code
                delivered = code == 0 and forum.get_agent(agent_id)["process_state"] not in {
                    ProcessState.BLOCKED.value, ProcessState.FAILED.value,
                }
                store.finish(
                    request_id, attempt_id, exit_code=code, delivery_succeeded=delivered,
                )
                held[agent_id] = 0 if delivered else store.held_through(agent_id)
                del active[agent_id]

            # Account for already pending work before considering extra capacity.
            enqueue_notifications()
            if managed and population.enabled and time.monotonic() >= next_idle_cleanup:
                population.park_idle_peers(available_agent_ids=peers)
                next_idle_cleanup = time.monotonic() + min(30.0, population.policy().idle_timeout)
            population_status = population.summary() if managed else None
            if managed and prepared.allow_join:
                if preparation is not None and preparation[1].done():
                    birth, task = preparation
                    preparation = None
                    agent_id = str(birth["agent"]["id"])
                    try:
                        peer = task.result()
                        if forum.get_agent(agent_id)["process_state"] == ProcessState.RETIRED.value:
                            population.finish_birth(birth["birth_id"], succeeded=False, error="Peer retired during preparation")
                            continue
                        connect_peer(peer)
                        population.finish_birth(birth["birth_id"], succeeded=True)
                        peers[agent_id] = peer
                        store.enqueue(agent_id, kind="start")
                    except Exception as error:
                        latest_codes[agent_id] = 1
                        population.finish_birth(birth["birth_id"], succeeded=False, error=str(error))
                if preparation is None and not population_status["exhausted"]:
                    birth = next((item for item in population.pending_births()
                                  if str(item["agent"]["id"]) not in peers), None)
                    if birth is None and population_status["enabled"]:
                        birth = population.reserve_birth(
                            initial=population_status["initial_remaining"] > 0,
                            execution_policy=policy, available_agent_ids=peers,
                        )
                    if birth:
                        # A large working copy must not stop mailboxes for peers
                        # already running. Only one copy is prepared at a time.
                        preparation = (birth, asyncio.create_task(asyncio.to_thread(
                            _registered_peer, forum, prepared.run, birth["agent"],
                        )))
                population_status = population.summary()

            records = enqueue_notifications()

            all_retired = all(
                records[agent_id]["process_state"] == ProcessState.RETIRED.value
                for agent_id in peers
            )
            can_still_join = bool(managed and prepared.allow_join and population_status["enabled"]
                                 and not population_status["births_exhausted"]
                                 and (population_status["initial_remaining"] or population_status["open_calls"]
                                      or population_status["pending_births"]))
            sessions_exhausted = bool(managed and population_status["births_exhausted"]
                                     and not population_status["pending_births"] and all(
                record["process_state"] in {ProcessState.RETIRED.value, ProcessState.BLOCKED.value}
                or not record.get("session_id") for key, record in records.items() if key in peers
            ))
            if not active and preparation is None and (all_retired and not can_still_join
                               or managed and population_status["exhausted"] or sessions_exhausted):
                for agent_id in peers:
                    if records[agent_id]["process_state"] == ProcessState.RETIRED.value:
                        store.cancel_queued(agent_id)
                return [latest_codes.get(agent_id, 0) for agent_id in dict.fromkeys((*peers, *latest_codes))]

            for request in store.queued():
                if len(active) >= policy.max_concurrent:
                    break
                agent_id = str(request["agent_id"])
                if agent_id not in peers or agent_id in active:
                    continue
                record = records[agent_id]
                if record["process_state"] == ProcessState.RETIRED.value:
                    store.cancel_queued(agent_id)
                    continue
                if (record["process_state"] == ProcessState.RUNNING.value
                        and _pid_is_alive(record["pid"]) and not _has_owned_attempt(forum, agent_id)):
                    continue
                peer = peers[agent_id]
                provider_active = sum(
                    peers[running_id].profile.provider == peer.profile.provider for running_id in active
                )
                if provider_active >= policy.provider_limit(peer.profile.provider.value):
                    continue
                notifications = communication.pending_batch(
                    agent_id, immediate=request["kind"] == "start"
                )
                if held.get(agent_id, 0):
                    # Include the instruction which unlocked a parked batch,
                    # even when older failed mentions fill the first page.
                    fresh = forum.pending_notifications(
                        agent_id, limit=1, after=held[agent_id], explicit_only=True
                    )
                    fresh_ids = {int(event["id"]) for event in fresh}
                    notifications = [dict(event, activation_trigger=True) for event in fresh] + [event for event in notifications
                                             if int(event["id"]) not in fresh_ids]
                # A subscription is never a fresh-session restart signal.
                if record["process_state"] in {
                    ProcessState.BLOCKED.value, ProcessState.FAILED.value
                } and request["kind"] != "start":
                    if not any(event.get("notification_reason") in {"mention", "broadcast"}
                               for event in notifications):
                        store.cancel_queued(agent_id)
                        continue
                if request["kind"] == "notification" and not notifications:
                    # Keep the durable request while a subscription burst settles.
                    # Cancel only when its deliveries were actually withdrawn/read.
                    if not forum.pending_notifications(agent_id, limit=1):
                        store.cancel_queued(agent_id)
                    continue
                page = forum.activity_page(agent_id, scope="following", limit=30)
                notification_ids = set(_delivery_ids(notifications))
                background = [event for event in page["items"]
                              if int(event["id"]) not in notification_ids]
                triggers, background, overflow = select_wake_context(notifications, background)
                if request["kind"] == "notification" and not triggers:
                    store.cancel_queued(agent_id)
                    continue
                request_id = int(request["id"])
                event_ids = _delivery_ids(triggers)
                attempt_id = store.claim(request_id, event_ids, policy)
                if attempt_id is None:
                    continue
                try:
                    invocation = invocation_for(
                        peer, record, str(request["kind"]), triggers, background, overflow
                    )
                    admitted = forum.reset_process_observation(
                        agent_id, clear_session=record["process_state"] == ProcessState.BLOCKED.value
                    )
                    if not admitted:
                        store.cancel_claim(request_id, attempt_id)
                        continue
                    peer.agent.update(forum.get_agent(agent_id))
                    active[agent_id] = (
                        asyncio.create_task(
                            runner(forum=forum, run_id=run_id, agent=peer.agent,
                                   profile=peer.profile, invocation=invocation, log_dir=log_dir),
                            name=peer.profile.name,
                        ),
                        request_id, attempt_id,
                    )
                except Exception:
                    forum.set_process_state(agent_id, ProcessState.FAILED, exit_code=1)
                    latest_codes[agent_id] = 1
                    store.finish(
                        request_id, attempt_id, exit_code=1,
                    )
                    held[agent_id] = store.held_through(agent_id)
            if active:
                await asyncio.wait(
                    [item[0] for item in active.values()], timeout=poll_interval,
                    return_when=asyncio.FIRST_COMPLETED,
                )
            else:
                await asyncio.sleep(poll_interval)
    finally:
        remaining = tuple(item[0] for item in active.values())
        for task in remaining:
            task.cancel()
        if remaining:
            await asyncio.gather(*remaining, return_exceptions=True)
        if preparation is not None:
            # Its reserved birth remains recoverable. Join the filesystem writer
            # before releasing the owner lock, including repeated cancellation.
            await _settle(asyncio.gather(preparation[1], return_exceptions=True))
        bridge.close()
        # Requests remain running until the next lock owner recovers them. No
        # notification is acknowledged on cancellation or uncertain completion.


def prepare_resume(
    *, forum: Forum, run_id: str,
    profile_names: Iterable[str] | None = None,
    fresh_sessions: bool = False, reset_processes: bool = True,
    additional_profiles: Iterable[AgentProfile] = (),
    run_lock: RunLock | None = None,
    workspace_mode: str | None = None,
) -> PreparedRun:
    if run_lock is not None:
        if run_lock.fd is None or run_lock.path != RunLock(forum.state_dir, run_id).path:
            raise ValueError("run_lock must be held for this run")
        return _prepare_resume(
            forum=forum, run_id=run_id, profile_names=profile_names,
            fresh_sessions=fresh_sessions, reset_processes=reset_processes,
            additional_profiles=additional_profiles,
            workspace_mode=workspace_mode,
        )
    with RunLock(forum.state_dir, run_id):
        return _prepare_resume(
            forum=forum, run_id=run_id, profile_names=profile_names,
            fresh_sessions=fresh_sessions, reset_processes=reset_processes,
            additional_profiles=additional_profiles,
            workspace_mode=workspace_mode,
        )
