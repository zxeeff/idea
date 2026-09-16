from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from idea.bridge import BridgeServer
from idea.commands import PEER_COMMANDS, dispatch_forum
from idea.domain import AgentProfile, Effort, Provider
from idea.forum import Forum
from idea.mcp_server import TOOL_NAMES


class McpServerTest(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.state_dir = self.root / ".idea"
        self.forum = Forum(self.state_dir)
        self.run = self.forum.create_run("Preserve structured messages", self.root)
        self.agent = self.forum.register_agent(
            self.run["id"],
            AgentProfile("mcp-peer", Provider.OPENAI, "test-model", Effort.LOW),
        )

    def environment(self, **values: str) -> dict[str, str]:
        env = os.environ.copy()
        source = str(Path(__file__).resolve().parents[1] / "src")
        env["PYTHONPATH"] = source + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        env.update({
            "IDEA_STATE_DIR": str(self.state_dir),
            "IDEA_RUN_ID": str(self.run["id"]),
            "IDEA_AGENT_ID": str(self.agent["id"]),
            "IDEA_AGENT_NAME": str(self.agent["name"]),
            **values,
        })
        return env

    def exchange(
        self, messages: list[dict], *, env: dict[str, str] | None = None,
    ) -> list[dict]:
        value = "".join(json.dumps(message, ensure_ascii=False) + "\n" for message in messages)
        result = subprocess.run(
            [sys.executable, "-m", "idea.mcp_server"],
            cwd=self.root,
            env=env or self.environment(),
            input=value,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=15,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("", result.stderr)
        return [json.loads(line) for line in result.stdout.splitlines()]

    @staticmethod
    def call(identifier: int, name: str, arguments: dict) -> dict:
        return {
            "jsonrpc": "2.0", "id": identifier, "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }

    @staticmethod
    def content(response: dict) -> dict:
        return json.loads(response["result"]["content"][0]["text"])

    def test_stdio_protocol_advertises_only_four_compact_tools(self) -> None:
        responses = self.exchange([
            {
                "jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18", "capabilities": {},
                    "clientInfo": {"name": "test", "version": "1"},
                },
            },
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        ])
        self.assertEqual([1, 2], [response["id"] for response in responses])
        self.assertEqual("2025-06-18", responses[0]["result"]["protocolVersion"])
        tools = responses[1]["result"]["tools"]
        self.assertEqual(list(TOOL_NAMES), [tool["name"] for tool in tools])
        self.assertEqual(4, len(tools))

    def test_structured_post_and_reply_preserve_shell_metacharacters_exactly(self) -> None:
        marker = self.root / "shell-was-here"
        body = (
            "[본문] `inline code` $(touch shell-was-here) ${HOME} $USER\n"
            "double=\"quoted\" single='quoted' slash=\\ 한글🙂 null=\x00 end"
        )
        post = self.exchange([self.call(1, "post", {"title": "정확한 본문", "body": body})])[0]
        self.assertNotIn("isError", post["result"])
        thread_id = self.content(post)["id"]
        self.assertEqual(body, self.forum.get_thread(thread_id)["body"])
        self.assertFalse(marker.exists())

        reply_body = "reply: `x` && echo wrong; > file | cat\n$(printf missing)\\tail"
        responses = self.exchange([
            self.call(2, "reply", {"thread_id": thread_id, "body": reply_body}),
            self.call(3, "forum", {"command": "read", "payload": {"thread_id": thread_id}}),
        ])
        self.assertEqual(reply_body, self.content(responses[0])["body"])
        read = self.content(responses[1])
        self.assertEqual(reply_body, read["comments"][0]["body"])
        self.assertFalse(marker.exists())

    def test_isolated_mcp_call_uses_the_identity_bound_bridge(self) -> None:
        workspace = self.root / "peer-workspace"
        workspace.mkdir()
        server = BridgeServer(
            self.forum,
            self.run["id"],
            lambda agent_id, command, payload: dispatch_forum(
                self.forum, self.run["id"], agent_id, command, payload,
            ),
            allowed_commands=PEER_COMMANDS,
        )
        mailbox = server.register(self.agent["id"], workspace)
        stopped = threading.Event()
        failures: list[BaseException] = []

        def poll() -> None:
            while not stopped.is_set():
                try:
                    server.poll()
                except BaseException as error:  # surfaced in the test thread
                    failures.append(error)
                    return
                time.sleep(0.001)

        worker = threading.Thread(target=poll, daemon=True)
        worker.start()
        self.addCleanup(server.close)
        try:
            body = "bridge keeps `code`, $(commands), quotes \"' and 새 줄\nexact"
            response = self.exchange(
                [self.call(1, "post", {"title": "Via bridge", "body": body})],
                env=self.environment(IDEA_BRIDGE_DIR=str(mailbox), IDEA_STATE_DIR=str(mailbox)),
            )[0]
            request_id = "1" * 32
            arguments = {
                "title": "Idempotent native post", "body": "one logical operation",
                "request_id": request_id,
            }
            first = self.exchange(
                [self.call(2, "post", arguments)],
                env=self.environment(IDEA_BRIDGE_DIR=str(mailbox), IDEA_STATE_DIR=str(mailbox)),
            )[0]
            second = self.exchange(
                [self.call(3, "post", arguments)],
                env=self.environment(IDEA_BRIDGE_DIR=str(mailbox), IDEA_STATE_DIR=str(mailbox)),
            )[0]
        finally:
            stopped.set()
            worker.join(timeout=2)
        self.assertFalse(failures)
        self.assertFalse(worker.is_alive())
        thread = self.forum.get_thread(self.content(response)["id"])
        self.assertEqual(self.agent["name"], thread["author"])
        self.assertEqual(body, thread["body"])

        self.assertEqual(self.content(first)["id"], self.content(second)["id"])
        matching = [
            item for item in self.forum.list_threads(self.run["id"], limit=20)
            if item["title"] == "Idempotent native post"
        ]
        self.assertEqual(1, len(matching))

    def test_reply_trigger_infers_the_activation_event_from_environment(self) -> None:
        thread = self.forum.create_thread(
            self.run["id"], "human", "Direct request", f"Please respond @{self.agent['name']}",
        )
        response = self.exchange(
            [self.call(1, "reply_trigger", {"body": "Structured direct response"})],
            env=self.environment(
                IDEA_TRIGGER_EVENT_ID=str(thread["event_id"]),
                IDEA_TRIGGER_THREAD_ID=str(thread["id"]),
            ),
        )[0]
        reply = self.content(response)
        self.assertEqual(thread["id"], reply["thread_id"])
        self.assertEqual(thread["event_id"], reply["provenance"]["reply_to_event_id"])


if __name__ == "__main__":
    unittest.main()
