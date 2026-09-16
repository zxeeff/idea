"""Small stdio MCP server for identity-bound IDEA knowledge-board operations."""

from __future__ import annotations

import json
import os
import sys
from typing import Any, Mapping

from .commands import PEER_COMMANDS, dispatch_forum
from .forum import Forum


SERVER_NAME = "idea"
SERVER_VERSION = "0.3.0"
PROTOCOL_VERSION = "2025-11-25"
SUPPORTED_PROTOCOLS = frozenset({"2024-11-05", "2025-03-26", "2025-06-18", PROTOCOL_VERSION})
TOOL_NAMES = ("post", "reply", "forum")
MAX_BYTES = 2 * 1024 * 1024


def _object_schema(properties: Mapping[str, Any], required: tuple[str, ...]) -> dict[str, Any]:
    return {"type": "object", "properties": dict(properties), "required": list(required),
            "additionalProperties": False}


TOOLS: tuple[dict[str, Any], ...] = (
    {
        "name": "post",
        "description": "Publish a concise finding on the public IDEA knowledge board.",
        "inputSchema": _object_schema(
            {"title": {"type": "string", "minLength": 1}, "body": {"type": "string"}},
            ("title", "body"),
        ),
    },
    {
        "name": "reply",
        "description": "Reply to a selected IDEA knowledge-board post.",
        "inputSchema": _object_schema(
            {"thread_id": {"type": "string", "minLength": 1}, "body": {"type": "string"}},
            ("thread_id", "body"),
        ),
    },
    {
        "name": "forum",
        "description": "Read, search, or attach a file through another knowledge-board operation.",
        "inputSchema": _object_schema(
            {"command": {"type": "string", "enum": sorted(PEER_COMMANDS)}, "payload": {"type": "object"}},
            ("command", "payload"),
        ),
    },
)


class ForumTools:
    """Route MCP calls through the same small command surface as the CLI."""

    def __init__(self, environ: Mapping[str, str] | None = None):
        self.environ = dict(os.environ if environ is None else environ)

    def call(self, name: str, arguments: Any) -> Any:
        if not isinstance(arguments, dict):
            raise ValueError("tool arguments must be a JSON object")
        if name == "post":
            command, payload = "post", arguments
        elif name == "reply":
            command, payload = "reply", arguments
        elif name == "forum":
            command, payload = arguments.get("command"), arguments.get("payload")
            if not isinstance(command, str) or command not in PEER_COMMANDS:
                raise ValueError("unknown IDEA board command")
            if not isinstance(payload, dict):
                raise ValueError("forum payload must be a JSON object")
        else:
            raise ValueError("unknown IDEA tool")
        state_dir = self.environ.get("IDEA_STATE_DIR")
        run_id = self.environ.get("IDEA_RUN_ID")
        agent_id = self.environ.get("IDEA_AGENT_ID")
        if not state_dir or not run_id or not agent_id:
            raise ValueError("IDEA board identity is unavailable")
        return dispatch_forum(
            Forum(state_dir), run_id, agent_id, command, dict(payload),
            author=self.environ.get("IDEA_AGENT_NAME"),
        )


def _error(identifier: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": identifier, "error": {"code": code, "message": message}}


def _result(identifier: Any, value: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": identifier, "result": value}


def _tool_result(value: Any, *, error: bool = False) -> dict[str, Any]:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    result: dict[str, Any] = {"content": [{"type": "text", "text": text}]}
    if error:
        result["isError"] = True
    return result


def handle_message(message: Any, tools: ForumTools) -> dict[str, Any] | None:
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
        return _error(message.get("id") if isinstance(message, dict) else None, -32600, "Invalid Request")
    identifier = message.get("id")
    if "id" not in message:
        return None
    if isinstance(identifier, bool) or not isinstance(identifier, (str, int)):
        return _error(None, -32600, "Invalid Request")
    method = message.get("method")
    params = message.get("params", {})
    if not isinstance(method, str) or not isinstance(params, dict):
        return _error(identifier, -32600, "Invalid Request")
    if method == "initialize":
        requested = params.get("protocolVersion")
        return _result(identifier, {
            "protocolVersion": requested if requested in SUPPORTED_PROTOCOLS else PROTOCOL_VERSION,
            "capabilities": {"tools": {}}, "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        })
    if method == "ping":
        return _result(identifier, {})
    if method == "tools/list":
        return _result(identifier, {"tools": list(TOOLS)})
    if method == "tools/call":
        name, arguments = params.get("name"), params.get("arguments", {})
        if not isinstance(name, str):
            return _error(identifier, -32602, "Invalid params")
        try:
            return _result(identifier, _tool_result(tools.call(name, arguments)))
        except (KeyError, TypeError, ValueError) as error:
            return _result(identifier, _tool_result(f"board operation failed ({type(error).__name__})", error=True))
    return _error(identifier, -32601, "Method not found")


def serve_stdio(environ: Mapping[str, str] | None = None) -> None:
    tools = ForumTools(environ)
    while line := sys.stdin.buffer.readline(MAX_BYTES + 1):
        if len(line) > MAX_BYTES or not line.endswith(b"\n"):
            continue
        try:
            response = handle_message(json.loads(line), tools)
        except (json.JSONDecodeError, UnicodeDecodeError):
            response = _error(None, -32700, "Parse error")
        if response is not None:
            sys.stdout.write(json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    serve_stdio()
