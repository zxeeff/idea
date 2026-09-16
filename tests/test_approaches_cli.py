from __future__ import annotations

import io
import json
import os
import tempfile
import threading
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from idea import cli
from idea.approaches import ApproachStore
from idea.bridge import BridgeServer
from idea.commands import dispatch_forum
from idea.domain import AgentProfile, Effort, Provider
from idea.forum import Forum


class ApproachesCliTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.forum = Forum(self.root / "state")
        self.run = self.forum.create_run("Independent investigations", self.root)
        self.peer = self.forum.register_agent(self.run["id"], AgentProfile("reader", Provider.OPENAI, "test", Effort.LOW))
        self.thread = self.forum.create_thread(self.run["id"], "human", "First idea", "Original evidence")

    def command(self, *arguments, environment=None):
        output = io.StringIO()
        base = ["forum"] if environment else ["forum", "--state-dir", str(self.forum.state_dir), "--run", self.run["id"]]
        with patch.dict(os.environ, environment or {}, clear=True), redirect_stdout(output):
            result = cli.main([*base, *arguments, "--json"])
        self.assertEqual(0, result, output.getvalue())
        return json.loads(output.getvalue())

    def approach(self, thread=None, **options):
        args = ["approach", (thread or self.thread)["id"], "--hypothesis", options.get("hypothesis", "Reusable evidence"), "--next-check", "Compare the two cases"]
        if options.get("parent"):
            args += ["--parent", options["parent"]]
        return self.command(*args)

    def test_create_read_join_mine_members_and_leave_preserve_peer_choice(self):
        approach = self.approach()
        self.assertEqual("Reusable evidence", self.command("read-approach", approach["id"])["hypothesis"])
        self.command("join", approach["id"], "--agent-id", self.peer["id"], "--focus", "Check boundary cases", "--wake")
        mine = self.command("approaches", "--mine", "--agent-id", self.peer["id"])
        self.assertEqual([approach["id"]], [item["id"] for item in mine["items"]])
        member = self.command("members", approach["id"], "--limit", "1")["items"][0]
        self.assertEqual(self.peer["id"], member["agent_id"])
        self.assertEqual("Check boundary cases", member["focus"])
        self.assertNotIn("session_id", member)
        self.command("leave", approach["id"], "--agent-id", self.peer["id"])
        self.assertEqual([], self.command("approaches", "--mine", "--agent-id", self.peer["id"])["items"])
        self.assertEqual(1, len(self.forum.list_agents(self.run["id"])))

    def test_approach_search_pagination_and_parent_reference(self):
        parent = self.approach(hypothesis="literal_% parent")
        expected = {parent["id"]}
        for number in range(6):
            thread = self.forum.create_thread(self.run["id"], "human", f"Idea {number}", "Details")
            expected.add(self.approach(thread, parent=parent["id"], hypothesis=f"Variation {number}")["id"])
        literal = self.command("approaches", "--query", "literal_%")
        self.assertEqual([parent["id"]], [item["id"] for item in literal["items"]])
        seen = []
        cursor = None
        while True:
            page = self.command("approaches", "--limit", "2", *(["--after", cursor] if cursor else []))
            seen.extend(item["id"] for item in page["items"])
            cursor = page["next_cursor"]
            if not cursor:
                break
        self.assertEqual(expected, set(seen))
        self.assertEqual(len(expected), len(seen))

    def test_report_full_round_trip_and_adoption_keep_evidence_and_conditions(self):
        source = self.approach()
        other_thread = self.forum.create_thread(self.run["id"], "human", "Second idea", "Different conditions")
        target = self.approach(other_thread, hypothesis="Try related case")
        verification = self.forum.add_comment(self.thread["id"], "human", "Checked sample", reply_to_event_id=self.thread["event_id"], relation="verifies", validation="One fixture only")
        report = self.command("report", source["id"], "--summary", "Works in the sample", "--conditions", "Small fixture only", "--open-questions", "Larger input?", "--source-event", str(self.thread["event_id"]), "--source-event", str(verification["event_id"]), "--validation-event", str(verification["event_id"]))
        read = self.command("read-report", report["id"])
        self.assertEqual("Small fixture only", read["conditions"])
        self.assertEqual([verification["event_id"]], read["validation_event_ids"])
        found = self.command("reports", "--approach", source["id"], "--query", "sample", "--limit", "1")
        self.assertEqual(report["id"], found["items"][0]["id"])
        self.command("adopt", report["id"], "--to", target["id"], "--application", "Repeat under the second idea's boundary condition")
        comments = self.forum.get_thread(other_thread["id"])["comments"]
        self.assertTrue(any(report["id"] in item["body"] for item in comments))
        self.assertTrue(any("boundary condition" in item["body"] for item in comments))
        replacement = self.command("report", source["id"], "--summary", "Refined sample result", "--conditions", "Same small fixture", "--source-event", str(self.thread["event_id"]), "--supersedes", report["id"])
        self.assertEqual(report["id"], replacement["supersedes_report_id"])
        self.assertTrue(self.command("read-report", report["id"])["is_superseded"])

    def test_missing_sources_and_unidentified_join_do_not_write(self):
        approach = self.approach()
        before = self.forum.activity_high_water(self.run["id"])
        with patch.dict(os.environ, {}, clear=True), redirect_stderr(io.StringIO()):
            result = cli.main(["forum", "--state-dir", str(self.forum.state_dir), "--run", self.run["id"], "join", approach["id"]])
        self.assertEqual(2, result)
        with self.assertRaises(SystemExit), redirect_stderr(io.StringIO()):
            cli.forum_parser().parse_args(["report", approach["id"], "--summary", "Claim", "--conditions", "Some conditions"])
        self.assertEqual(before, self.forum.activity_high_water(self.run["id"]))

    def test_isolated_mailbox_uses_registered_identity_for_new_commands(self):
        workspace = self.root / "private"
        workspace.mkdir()
        server = BridgeServer(self.forum, self.run["id"], lambda agent_id, command, payload: dispatch_forum(self.forum, self.run["id"], agent_id, command, payload))
        mailbox = server.register(self.peer["id"], workspace)
        stopped = threading.Event()
        def poll():
            while not stopped.wait(0.01):
                server.poll()
        worker = threading.Thread(target=poll, daemon=True)
        worker.start()
        try:
            environment = {"IDEA_BRIDGE_DIR": str(mailbox)}
            approach = self.command("approach", self.thread["id"], "--hypothesis", "Mailbox idea", "--next-check", "Read original", environment=environment)
            self.assertEqual("reader", approach["author"])
            self.command("join", approach["id"], "--focus", "Original source", environment=environment)
            self.assertEqual([approach["id"]], [item["id"] for item in self.command("approaches", "--mine", environment=environment)["items"]])
            report = self.command("report", approach["id"], "--summary", "Mailbox finding", "--conditions", "Sample only", "--source-event", str(self.thread["event_id"]), environment=environment)
            self.assertEqual("reader", report["author"])
        finally:
            stopped.set()
            worker.join(timeout=2)
            server.close()


if __name__ == "__main__":
    unittest.main()
