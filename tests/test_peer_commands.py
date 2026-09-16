from __future__ import annotations

import base64
import io
import os
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch

from idea.cli import main
from idea.commands import dispatch_forum, public_artifact
from idea.domain import AgentProfile, Effort, Provider
from idea.forum import Forum
from idea.workspaces import WorkspaceStore


class PeerCommandsTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.workspace = Path(self.directory.name).resolve()
        (self.workspace / "sample.txt").write_text("initial\n")
        self.forum = Forum(self.workspace / ".idea-swarm")
        self.run = self.forum.create_run("Improve the sample", self.workspace)
        self.other_run = self.forum.create_run("Another project", self.workspace)
        self.agent = self.forum.register_agent(self.run["id"], AgentProfile("writer", Provider.OPENAI, "fake", Effort.LOW))
        self.other = self.forum.create_thread(self.other_run["id"], "human", "Other run", "Separate discussion")
        self.thread = self.forum.create_thread(self.run["id"], "human", "Current discussion", "Public within this run")

    def command(self, command, **payload):
        return dispatch_forum(self.forum, self.run["id"], self.agent["id"], command, payload)

    def test_target_operations_cannot_cross_run_boundary(self):
        for command, payload in (
            ("read", {"thread_id": self.other["id"]}),
            ("reply", {"thread_id": self.other["id"], "body": "Wrong target"}),
            ("follow", {"thread_id": self.other["id"]}),
            ("attach", {"thread_id": self.other["id"], "filename": "sample.bin", "data_base64": "AA=="}),
        ):
            with self.subTest(command=command), self.assertRaisesRegex(ValueError, "another run"):
                self.command(command, **payload)
        self.assertEqual([], self.forum.get_thread(self.other["id"])["comments"])

    def test_attachment_carries_bytes_and_preserves_author_without_exposing_host_path(self):
        result = self.command("attach", filename="sample.bin", data_base64=base64.b64encode(b"\x00\xff").decode(),
                              thread_id=self.thread["id"], description="Binary evidence")
        self.assertEqual("writer", result["author"])
        self.assertEqual(2, result["size"])
        self.assertNotIn("stored_path", result)
        self.assertEqual("sample.bin", result["original_name"])

    def test_versioned_artifact_read_and_list_are_public_without_host_paths(self):
        copies = WorkspaceStore(self.forum, self.run["id"])
        copies.configure("isolated")
        private = copies.prepare(self.agent["id"])
        (private / "sample.txt").write_text("contribution\n")
        result = public_artifact(copies.publish(self.agent["id"], note="Changed sample", validation="Read file"))
        self.assertNotIn("patch_path", result)
        listed = self.command("artifacts", limit=1)
        self.assertEqual(result["id"], listed["items"][0]["id"])
        self.assertNotIn("patch_path", listed["items"][0])
        read = self.command("artifact", artifact_id=result["id"])
        self.assertIn(b"contribution", base64.b64decode(read["patch_base64"]))
        self.assertEqual("initial\n", (self.workspace / "sample.txt").read_text())
        with self.assertRaises(KeyError):
            WorkspaceStore(self.forum, self.other_run["id"]).get_artifact(result["id"])

    def test_peer_cannot_start_nested_launcher_or_integrate_original(self):
        for arguments in (["run", "another goal"], ["resume"], ["integrate", "any-artifact"]):
            with patch.dict(os.environ, {"IDEA_AGENT_ID": self.agent["id"]}), redirect_stderr(io.StringIO()):
                self.assertEqual(2, main(arguments))


if __name__ == "__main__":
    unittest.main()
