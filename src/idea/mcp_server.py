"""Small stdio MCP server for identity-bound IDEA forum operations.

The model sends message text as JSON strings, so shell parsing cannot alter
backticks, dollar expressions, quotes, backslashes, or newlines. Current runs
use the shared forum directly; the older mailbox transport remains available
for compatibility.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Mapping

from .bridge import BridgeClient, BridgeError, MAX_BYTES
from .commands import PEER_COMMANDS, dispatch_forum
from .forum import Forum


SERVER_NAME = "idea"
SERVER_VERSION = "0.2.0"
PROTOCOL_VERSION = "2025-11-25"
SUPPORTED_PROTOCOLS = frozenset({
    "2024-11-05", "2025-03-26", "2025-06-18", PROTOCOL_VERSION,
})
TOOL_NAMES = ("post", "reply", "reply_trigger", "forum")

_RELATIONS = ("reply", "supports", "challenges", "verifies", "retracts", "supersedes")
_PROVENANCE_PROPERTIES: dict[str, Any] = {
    "reply_to_event_id": {"type": "integer", "minimum": 1},
    "relation": {"type": "string", "enum": list(_RELATIONS)},
    "artifact_id": {"type": "string"},
    "validation": {"type": "string"},
    "evidence_event_ids": {
        "type": "array",
        "items": {"type": "integer", "minimum": 1},
        "maxItems": 16,
    },
}
_REQUEST_ID_PROPERTY = {
    "request_id": {
        "type": "string",
        "pattern": "^[a-f0-9]{32}$",
        "description": "Reuse only to recover the same timed-out mailbox operation.",
    }
}


def _object_schema(
    properties: Mapping[str, Any], required: tuple[str, ...],
) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": dict(properties),
        "required": list(required),
        "additionalProperties": False,
    }


TOOLS: tuple[dict[str, Any], ...] = (
    {
        "name": "post",
        "description": "Create a public IDEA forum thread with the complete title and body.",
        "inputSchema": _object_schema(
            {
                "title": {"type": "string", "minLength": 1},
                "body": {"type": "string"},
                **_REQUEST_ID_PROPERTY,
            },
            ("title", "body"),
        ),
    },
    {
        "name": "reply",
        "description": "Reply to a chosen IDEA forum thread, optionally recording evidence provenance.",
        "inputSchema": _object_schema(
            {
                "thread_id": {"type": "string", "minLength": 1},
                "body": {"type": "string"},
                **_PROVENANCE_PROPERTIES,
                **_REQUEST_ID_PROPERTY,
            },
            ("thread_id", "body"),
        ),
    },
    {
        "name": "reply_trigger",
        "description": (
            "Reply to the explicit IDEA mention that activated this turn. "
            "The event is inferred when omitted."
        ),
        "inputSchema": _object_schema(
            {
                "body": {"type": "string"},
                "event": {"type": "integer", "minimum": 1},
                **_PROVENANCE_PROPERTIES,
                **_REQUEST_ID_PROPERTY,
            },
            ("body",),
        ),
    },
    {
        "name": "forum",
        "description": (
            "Run any other IDEA forum operation with its command name and JSON payload. "
            "Use the dedicated tools for ordinary posts and replies."
        ),
        "inputSchema": _object_schema(
            {
                "command": {"type": "string", "enum": sorted(PEER_COMMANDS)},
                "payload": {"type": "object"},
                **_REQUEST_ID_PROPERTY,
            },
            ("command", "payload"),
        ),
    },
)


class ForumTools:
    """Route MCP calls through the same public command surface as the CLI."""

    def __init__(self, environ: Mapping[str, str] | None = None):
        self.environ = dict(os.environ if environ is None else environ)

    def _payload_for_trigger(self, payload: dict[str, Any]) -> dict[str, Any]:
        if "event" in payload:
            return payload
        event = self.environ.get("IDEA_TRIGGER_EVENT_ID")
        thread = self.environ.get("IDEA_TRIGGER_THREAD_ID")
        if event is None and thread is None:
            return payload
        result = dict(payload)
        if event is not None:
            try:
                result["event"] = int(event)
            except ValueError as error:
                raise ValueError("IDEA_TRIGGER_EVENT_ID must be an integer") from error
        if thread:
            result["trigger_thread_id"] = thread
        return result

    def call(self, name: str, arguments: Any) -> Any:
        if not isinstance(arguments, dict):
            raise ValueError("tool arguments must be a JSON object")
        arguments = dict(arguments)
        request_id = arguments.pop("request_id", None)
        if name == "post":
            command, payload = "post", arguments
        elif name == "reply":
            command, payload = "reply", arguments
        elif name == "reply_trigger":
            command, payload = "reply-trigger", self._payload_for_trigger(arguments)
        elif name == "forum":
            command = arguments.get("command")
            payload = arguments.get("payload")
            if not isinstance(command, str) or command not in PEER_COMMANDS:
                raise ValueError("unknown IDEA forum command")
            if not isinstance(payload, dict):
                raise ValueError("forum payload must be a JSON object")
            payload = dict(payload)
            if command == "reply-trigger":
                payload = self._payload_for_trigger(payload)
        else:
            raise ValueError("unknown IDEA tool")

        bridge_dir = self.environ.get("IDEA_BRIDGE_DIR")
        if bridge_dir:
            return BridgeClient(Path(bridge_dir)).call(command, payload, request_id=request_id)
        if request_id is not None:
            raise ValueError("request_id is available only through an isolated peer mailbox")

        state_dir = self.environ.get("IDEA_STATE_DIR")
        run_id = self.environ.get("IDEA_RUN_ID")
        agent_id = self.environ.get("IDEA_AGENT_ID")
        if not state_dir or not run_id or not agent_id:
            raise ValueError("IDEA forum identity is unavailable")
        return dispatch_forum(
            Forum(Path(state_dir)), run_id, agent_id, command, payload,
            author=self.environ.get("IDEA_AGENT_NAME"),
        )


def _error(identifier: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": identifier, "error": {"code": code, "message": message}}


def _result(identifier: Any, value: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": identifier, "result": value}


def _tool_result(value: Any, *, error: bool = False) -> dict[str, Any]:
    if isinstance(value, str):
        text = value
    else:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    result: dict[str, Any] = {"content": [{"type": "text", "text": text}]}
    if error:
        result["isError"] = True
    return result


def handle_message(message: Any, tools: ForumTools) -> dict[str, Any] | None:
    """Handle one MCP JSON-RPC message; notifications intentionally return None."""

    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
        return _error(message.get("id") if isinstance(message, dict) else None, -32600,
                      "Invalid Request")
    identifier = message.get("id")
    is_request = "id" in message
    if is_request and (isinstance(identifier, bool) or not isinstance(identifier, (str, int))):
        return _error(None, -32600, "Invalid Request")
    method = message.get("method")
    if not isinstance(method, str):
        return _error(identifier if is_request else None, -32600, "Invalid Request")
    if not is_request:
        return None

    params = message.get("params", {})
    if not isinstance(params, dict):
        return _error(identifier, -32602, "Invalid params")
    if method == "initialize":
        requested = params.get("protocolVersion")
        protocol = requested if requested in SUPPORTED_PROTOCOLS else PROTOCOL_VERSION
        return _result(identifier, {
            "protocolVersion": protocol,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        })
    if method == "ping":
        return _result(identifier, {})
    if method == "tools/list":
        if params.get("cursor") is not None:
            return _error(identifier, -32602, "Invalid cursor")
        return _result(identifier, {"tools": list(TOOLS)})
    if method == "tools/call":
        name = params.get("name")
        arguments = params.get("arguments", {})
        if not isinstance(name, str):
            return _error(identifier, -32602, "Invalid params")
        try:
            value = tools.call(name, arguments)
            return _result(identifier, _tool_result(value))
        except (BridgeError, KeyError, TypeError, ValueError) as error:
            return _result(identifier, _tool_result(str(error), error=True))
        except Exception as error:  # keep host details out of provider context
            return _result(
                identifier,
                _tool_result(f"forum operation failed ({type(error).__name__})", error=True),
            )
    return _error(identifier, -32601, "Method not found")


def _write(message: dict[str, Any]) -> None:
    data = json.dumps(
        message, ensure_ascii=False, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")
    sys.stdout.buffer.write(data + b"\n")
    sys.stdout.buffer.flush()


def _load(data: bytes) -> Any:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    def constant(value: str) -> None:
        raise ValueError(f"invalid JSON constant: {value}")

    return json.loads(data, object_pairs_hook=pairs, parse_constant=constant)


def main() -> int:
    tools = ForumTools()
    while True:
        line = sys.stdin.buffer.readline(MAX_BYTES + 1)
        if not line:
            return 0
        if len(line) > MAX_BYTES or not line.endswith(b"\n"):
            while line and not line.endswith(b"\n"):
                line = sys.stdin.buffer.readline(MAX_BYTES + 1)
            _write(_error(None, -32700, "Parse error"))
            continue
        try:
            message = _load(line)
        except (UnicodeError, ValueError, RecursionError):
            _write(_error(None, -32700, "Parse error"))
            continue
        response = handle_message(message, tools)
        if response is not None:
            _write(response)


if __name__ == "__main__":
    raise SystemExit(main())
