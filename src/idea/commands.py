"""Forum operations shared by the CLI and native peer tools."""

from __future__ import annotations

import base64
import tempfile
from pathlib import Path
from typing import Any

from .forum import Forum


PEER_COMMANDS = frozenset({
    "recent", "inbox", "discover", "peers", "follow", "unfollow", "following",
    "read", "changes", "search", "post", "reply", "reply-trigger", "attach", "retire",
    "recruit", "calls", "volunteer", "cancel-call", "templates", "population",
    "artifacts", "artifact",
    "approach", "approaches", "read-approach", "join", "leave", "members",
    "report", "reports", "read-report", "adopt",
})
MAX_FILE_BYTES = 4 * 1024 * 1024


def public_artifact(value: dict[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items()
            if key not in {"patch_path", "path", "workspace", "manifest_path"}}


def dispatch_forum(
    forum: Forum, run_id: str, agent_id: str | None, command: str,
    payload: dict[str, Any], *, author: str | None = None,
) -> Any:
    """The bridge supplies identity itself; a payload cannot select another peer."""
    if command not in PEER_COMMANDS:
        raise ValueError("unsupported peer command")
    if agent_id:
        agent = forum.get_agent(agent_id)
        if agent["run_id"] != run_id:
            raise ValueError("peer belongs to another run")
        author = author or str(agent["name"])
    author = author or "human"

    def identity() -> str:
        if not agent_id:
            raise ValueError("agent id is required; run inside a peer or pass --agent-id")
        return agent_id

    def thread(value: Any) -> str:
        if not isinstance(value, str):
            raise ValueError("thread id must be a string")
        record = forum.get_thread(value, include_comments=False)
        if record["run_id"] != run_id:
            raise ValueError("thread belongs to another run")
        return value

    def reply_metadata() -> dict[str, Any]:
        fields = {name: payload[name] for name in (
            "reply_to_event_id", "relation", "artifact_id", "validation", "evidence_event_ids"
        ) if name in payload}
        # argparse's optional repeated flag uses None when omitted.
        if fields.get("evidence_event_ids") is None:
            fields.pop("evidence_event_ids", None)
        return fields

    limit, after = payload.get("limit", 30), payload.get("after")
    if command in {"approach", "approaches", "read-approach", "join", "leave", "members"}:
        from .approaches import ApproachStore

        approaches = ApproachStore(forum, run_id)
        if command == "approach":
            return approaches.create(
                thread(payload.get("thread_id")), author,
                hypothesis=payload["hypothesis"], next_check=payload["next_check"],
                parent_id=payload.get("parent_id"),
            )
        if command == "approaches":
            mine = payload.get("mine", False)
            if not isinstance(mine, bool):
                raise ValueError("mine must be a boolean")
            return approaches.list(
                query=payload.get("query", ""), limit=limit, after=after,
                agent_id=identity() if mine else None, thread_id=payload.get("thread_id"),
            )
        if command == "read-approach":
            return approaches.get(payload["approach_id"])
        if command == "join":
            return approaches.join(payload["approach_id"], identity(), focus=payload.get("focus", ""),
                                   wake=payload.get("wake", False))
        if command == "leave":
            return approaches.leave(payload["approach_id"], identity())
        return approaches.members(payload["approach_id"], limit=limit, after=after)
    if command in {"report", "reports", "read-report", "adopt"}:
        from .reports import ReportStore

        reports = ReportStore(forum, run_id)
        if command == "report":
            validation_ids = payload.get("validation_event_ids")
            return reports.publish(
                payload["approach_id"], author, summary=payload["summary"], conditions=payload["conditions"],
                open_questions=payload.get("open_questions", ""), source_event_ids=payload["source_event_ids"],
                artifact_id=payload.get("artifact_id"), validation_event_ids=() if validation_ids is None else validation_ids,
                supersedes_report_id=payload.get("supersedes_report_id"),
            )
        if command == "reports":
            return reports.list(approach_id=payload.get("approach_id"), query=payload.get("query", ""),
                                limit=limit, after=after, thread_id=payload.get("thread_id"))
        if command == "read-report":
            return reports.get(payload["report_id"])
        return reports.adopt(payload["report_id"], payload["target_approach_id"], author,
                             application=payload["application"])
    if command in {"recent", "search"}:
        items, cursor = forum.list_thread_summaries(
            run_id, limit=limit, before=after,
            query=payload.get("query", "") if command == "search" else "",
        )
        return {"items": items, "next_cursor": cursor}
    if command in {"inbox", "discover"}:
        value = forum.activity_page(
            identity(), scope="all" if command == "discover" else payload.get("scope", "following"),
            limit=limit, after=(after or 0) if command == "discover" else after,
        )
        if command == "inbox":
            forum.mark_activity_seen(identity(), value["through_id"])
        return value
    if command == "peers":
        return forum.search_agents(run_id, query=payload.get("query", ""), limit=limit, after=after)
    if command == "following":
        return forum.list_subscriptions(identity(), limit=limit, after=after)
    if command in {"follow", "unfollow"}:
        target = thread(payload.get("thread_id"))
        if command == "follow":
            wake = payload.get("wake", False)
            if not isinstance(wake, bool):
                raise ValueError("wake must be a boolean")
            return forum.subscribe(identity(), target, wake=wake)
        return forum.unsubscribe(identity(), target)
    if command == "read":
        target = thread(payload.get("thread_id"))
        value = forum.get_thread(target, include_comments=False)
        page = forum.comments_page(target, limit=limit, after=after)
        return value | {"comments": page["items"], "next_cursor": page["next_cursor"]}
    if command == "changes":
        return forum.thread_changes(
            thread(payload.get("thread_id")), after_event=payload.get("after_event", 0),
            through_event=payload.get("through_event"), limit=limit,
        )
    if command == "post":
        return forum.create_thread(run_id, author, payload["title"], payload["body"])
    if command == "reply":
        return forum.add_comment(thread(payload.get("thread_id")), author, payload["body"], **reply_metadata())
    if command == "reply-trigger":
        trigger = forum.resolve_reply_trigger(identity(), payload.get("event"))
        hint = payload.get("trigger_thread_id")
        if hint and hint != trigger["thread_id"]:
            raise ValueError("trigger routing hints disagree; refusing to misroute the reply")
        metadata = reply_metadata()
        explicit_parent = metadata.get("reply_to_event_id")
        if explicit_parent is not None and (
            isinstance(explicit_parent, bool) or not isinstance(explicit_parent, int)
            or explicit_parent != int(trigger["id"])
        ):
            raise ValueError("reply_to_event_id disagrees with the resolved trigger event")
        metadata["reply_to_event_id"] = int(trigger["id"])
        value = forum.add_comment(thread(trigger["thread_id"]), author, payload["body"], **metadata)
        return value | {"trigger_event_id": int(trigger["id"]), "trigger_thread_id": trigger["thread_id"]}
    if command == "attach":
        filename = payload["filename"]
        if (not isinstance(filename, str) or not filename or filename in {".", ".."}
                or any(character in filename for character in ("/", "\\", "\0"))):
            raise ValueError("attachment filename must be a basename")
        encoded = payload["data_base64"]
        if not isinstance(encoded, str) or len(encoded) > ((MAX_FILE_BYTES + 2) // 3) * 4:
            raise ValueError("attachment exceeds 4 MiB")
        data = base64.b64decode(encoded, validate=True)
        if len(data) > MAX_FILE_BYTES:
            raise ValueError("attachment exceeds 4 MiB")
        target = thread(payload["thread_id"]) if payload.get("thread_id") else None
        with tempfile.TemporaryDirectory(prefix="idea-attachment-") as directory:
            source = Path(directory) / filename
            source.write_bytes(data)
            value = forum.add_attachment(run_id, author, source, thread_id=target,
                                         description=payload.get("description", ""))
        return {key: item for key, item in value.items() if key != "stored_path"}
    if command == "retire":
        value = forum.retire_agent(identity(), payload.get("reason", ""))
        return {key: item for key, item in value.items() if key not in {"session_id", "pid"}}
    if command in {"recruit", "calls", "volunteer", "cancel-call", "templates", "population"}:
        from .population import PopulationStore

        store = PopulationStore(forum, run_id)
        if command == "population":
            return store.summary()
        if command == "templates":
            return {"items": store.templates(), "next_cursor": None}
        if command == "calls":
            return store.list_calls(state=payload.get("state", "open"), limit=limit, after=after)
        if command == "recruit":
            return store.open_call(identity(), thread(payload.get("thread_id")), payload["reason"],
                                   template_id=payload.get("template_id"), ttl=payload.get("ttl"),
                                   request_key=payload.get("request_key"))
        if command == "volunteer":
            return store.volunteer(identity(), payload["call_id"])
        return store.cancel_call(identity(), payload["call_id"])
    from .workspaces import WorkspaceStore

    workspaces = WorkspaceStore(forum, run_id)
    if command == "artifacts":
        page = workspaces.list_artifacts(limit=limit, after=after)
        return page | {"items": [public_artifact(item) for item in page["items"]]}
    if command == "artifact":
        value = workspaces.get_artifact(payload["artifact_id"])
        path = Path(value["patch_path"])
        if path.stat().st_size > MAX_FILE_BYTES:
            raise ValueError("artifact exceeds the 4 MiB mailbox limit; inspect it locally")
        return workspaces.read_artifact(payload["artifact_id"])
    raise AssertionError(command)
