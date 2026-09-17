from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from idea.commands import PEER_COMMANDS, dispatch_forum
from idea.domain import AgentProfile, Effort, ProcessState, Provider
from idea.forum import Forum
from idea.launcher import prepare_run, run_reactor
from idea.mcp_server import ForumTools, TOOL_NAMES, handle_message
from idea.web import render_page


class KnowledgeBoardTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.workspace = Path(self.temp.name).resolve()
        self.forum = Forum(self.workspace / ".idea")
        self.run = self.forum.create_run("Find the defect", self.workspace)
        self.agent = self.forum.register_agent(
            self.run["id"], AgentProfile("reader", Provider.OPENAI, "test", Effort.LOW)
        )

    def test_board_only_exposes_read_publish_and_reply_operations(self) -> None:
        self.assertEqual({"recent", "read", "search", "post", "reply", "attach"}, PEER_COMMANDS)
        self.assertEqual(("post", "reply", "forum"), TOOL_NAMES)
        post = dispatch_forum(
            self.forum, self.run["id"], self.agent["id"], "post",
            {"title": "Observed boundary", "body": "@reader is ordinary text."},
        )
        reply = dispatch_forum(
            self.forum, self.run["id"], self.agent["id"], "reply",
            {"thread_id": post["id"], "body": "Confirmed with a regression case."},
        )
        loaded = dispatch_forum(
            self.forum, self.run["id"], self.agent["id"], "read", {"thread_id": post["id"]},
        )
        self.assertEqual("@reader is ordinary text.", loaded["body"])
        self.assertEqual([reply["id"]], [item["id"] for item in loaded["comments"]])
        with self.assertRaisesRegex(ValueError, "unsupported board command"):
            dispatch_forum(self.forum, self.run["id"], self.agent["id"], "follow", {})

    def test_reactor_runs_each_initial_peer_once_even_when_a_post_contains_a_tag(self) -> None:
        profiles = (
            AgentProfile("first", Provider.OPENAI, "test", Effort.LOW),
            AgentProfile("second", Provider.ANTHROPIC, "test", Effort.LOW),
        )
        prepared = prepare_run(
            forum=self.forum, goal="Find the defect", workspace=self.workspace, profiles=profiles,
        )
        calls: list[str] = []

        async def fake_runner(**kwargs):
            agent = kwargs["agent"]
            calls.append(kwargs["profile"].name)
            self.forum.set_process_state(agent["id"], ProcessState.RUNNING)
            if kwargs["profile"].name == "first":
                self.forum.create_thread(prepared.run["id"], "first", "Finding", "Please see @second")
            self.forum.set_process_state(agent["id"], ProcessState.DORMANT, exit_code=0)
            return 0

        codes = asyncio.run(run_reactor(
            forum=self.forum, prepared=prepared, runner=fake_runner, poll_interval=0.01,
        ))
        self.assertEqual([0, 0], codes)
        self.assertCountEqual(["first", "second"], calls)

    def test_goal_is_the_first_prompt_text_not_an_initial_board_post(self) -> None:
        goal = "Keep this exact user input first."
        prepared = prepare_run(
            forum=self.forum,
            goal=goal,
            workspace=self.workspace,
            profiles=(AgentProfile("writer", Provider.OPENAI, "test", Effort.LOW),),
        )
        self.assertEqual([], self.forum.list_threads(str(prepared.run["id"])))
        self.assertEqual(goal, prepared.peers[0].invocation.argv[-1])

    def test_board_page_has_no_coordination_controls(self) -> None:
        page = render_page(self.forum, self.run["id"])
        self.assertIn("Knowledge Board", page)
        for hidden_feature in ("@all", "알림", "접근법", "Peers", "태그"):
            self.assertNotIn(hidden_feature, page)

    def test_mcp_exposes_and_executes_only_board_tools(self) -> None:
        tools = ForumTools({
            "IDEA_STATE_DIR": str(self.forum.state_dir),
            "IDEA_RUN_ID": self.run["id"],
            "IDEA_AGENT_ID": self.agent["id"],
            "IDEA_AGENT_NAME": "reader",
        })
        created = tools.call("post", {"title": "MCP finding", "body": "verified"})
        recent = tools.call("forum", {"command": "recent", "payload": {}})
        self.assertEqual(created["id"], recent["items"][0]["id"])
        listed = handle_message(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}, tools,
        )
        self.assertEqual(["post", "reply", "forum"], [item["name"] for item in listed["result"]["tools"]])


if __name__ == "__main__":
    unittest.main()
