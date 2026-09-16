"""The deliberately small public interface of the shared knowledge board."""

from __future__ import annotations

import base64
import tempfile
from pathlib import Path
from typing import Any

from .forum import Forum


# Peers can publish a finding, read public findings, and reply. There are no
# coordination, recruitment, notification, or task-routing operations here.
PEER_COMMANDS = frozenset({"recent", "read", "search", "post", "reply", "attach"})
MAX_FILE_BYTES = 4 * 1024 * 1024


def dispatch_forum(
    forum: Forum, run_id: str, agent_id: str | None, command: str,
    payload: dict[str, Any], *, author: str | None = None,
) -> Any:
    """Execute a knowledge-board operation with a bound peer identity."""

    if command not in PEER_COMMANDS:
        raise ValueError("unsupported board command")
    if agent_id:
        agent = forum.get_agent(agent_id)
        if agent["run_id"] != run_id:
            raise ValueError("peer belongs to another run")
        author = author or str(agent["name"])
    author = author or "human"

    def thread(value: Any) -> str:
        if not isinstance(value, str):
            raise ValueError("thread id must be a string")
        record = forum.get_thread(value, include_comments=False)
        if record["run_id"] != run_id:
            raise ValueError("thread belongs to another run")
        return value

    limit, after = payload.get("limit", 30), payload.get("after")
    if command in {"recent", "search"}:
        items, cursor = forum.list_thread_summaries(
            run_id,
            limit=limit,
            before=after,
            query=payload.get("query", "") if command == "search" else "",
        )
        return {"items": items, "next_cursor": cursor}
    if command == "read":
        target = thread(payload.get("thread_id"))
        value = forum.get_thread(target, include_comments=False)
        page = forum.comments_page(target, limit=limit, after=after)
        return value | {"comments": page["items"], "next_cursor": page["next_cursor"]}
    if command == "post":
        return forum.create_thread(run_id, author, payload["title"], payload["body"])
    if command == "reply":
        return forum.add_comment(thread(payload.get("thread_id")), author, payload["body"])
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
            value = forum.add_attachment(
                run_id, author, source, thread_id=target, description=payload.get("description", ""),
            )
        return {key: item for key, item in value.items() if key != "stored_path"}
    raise AssertionError(command)
