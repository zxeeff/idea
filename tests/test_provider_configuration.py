from __future__ import annotations

import json
import tempfile
import tomllib
import unittest
from pathlib import Path

from idea.domain import AgentProfile, Effort, Provider
from idea.providers import build_invocation


class ProviderConfigurationTest(unittest.TestCase):
    def test_provider_invocations_expose_only_the_knowledge_board_tools(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory).resolve()
            for provider in (Provider.OPENAI, Provider.ANTHROPIC):
                with self.subTest(provider=provider):
                    invocation = build_invocation(
                        profile=AgentProfile("peer", provider, "model", Effort.MAX),
                        system_prompt="Work directly on the objective.",
                        task_prompt="OBJECTIVE:\nFix it",
                        workspace=workspace,
                        state_dir=workspace / ".idea",
                        run_id="run",
                        agent={"id": "agent", "name": "peer"},
                    )
                    if provider is Provider.OPENAI:
                        configs = tuple(
                            invocation.argv[index + 1]
                            for index, item in enumerate(invocation.argv[:-1])
                            if item == "--config"
                        )
                        enabled = next(item for item in configs if item.startswith("mcp_servers.idea.enabled_tools="))
                        self.assertEqual(["post", "reply", "forum"], tomllib.loads(enabled)["mcp_servers"]["idea"]["enabled_tools"])
                        self.assertIn("--dangerously-bypass-approvals-and-sandbox", invocation.argv)
                    else:
                        config = json.loads(invocation.argv[invocation.argv.index("--mcp-config") + 1])
                        self.assertEqual(
                            "mcp__idea__post,mcp__idea__reply,mcp__idea__forum",
                            invocation.argv[invocation.argv.index("--allowedTools") + 1],
                        )
                        self.assertEqual("stdio", config["mcpServers"]["idea"]["type"])
                        self.assertIn("--dangerously-skip-permissions", invocation.argv)


if __name__ == "__main__":
    unittest.main()
