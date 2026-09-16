from __future__ import annotations

import tempfile
import threading
import unittest
import uuid
from pathlib import Path

from idea.bridge import BridgeClient, BridgeRemoteError, BridgeServer
from idea.commands import PEER_COMMANDS, dispatch_forum
from idea.domain import AgentProfile, Effort, Provider
from idea.forum import Forum


class CommunicationBridgeTest(unittest.TestCase):
    """Exercise transport, identity-bound dispatch, and durable forum storage together."""

    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name).resolve()
        self.forum = Forum(self.root / "state")
        self.run = self.forum.create_run("Compare independently reported evidence", self.root)
        self.peer = self.forum.register_agent(
            self.run["id"], AgentProfile("reader", Provider.OPENAI, "fake", Effort.LOW)
        )
        self.workspace = self.root / "private-peer"
        self.workspace.mkdir()
        self.dispatched: list[str] = []

        def handler(agent_id: str, command: str, payload: dict):
            self.dispatched.append(command)
            return dispatch_forum(self.forum, self.run["id"], agent_id, command, payload)

        self.handler = handler
        self.server = self.new_server()
        self.mailbox = self.server.register(self.peer["id"], self.workspace)
        self.client = BridgeClient(self.mailbox, timeout=3, poll_interval=0.005)
        self.topic = self.forum.create_thread(self.run["id"], "human", "Claim", "@reader Original question")
        self.evidence = self.forum.create_thread(self.run["id"], "writer", "Evidence", "Independent source")

    def tearDown(self) -> None:
        self.server.close()
        self.directory.cleanup()

    def new_server(self) -> BridgeServer:
        return BridgeServer(self.forum, self.run["id"], self.handler, allowed_commands=PEER_COMMANDS)

    def exchange(self, command: str, payload: dict, *, request_id: str | None = None):
        result = {}
        finished = threading.Event()

        def call():
            try:
                result["value"] = self.client.call(command, payload, request_id=request_id)
            except BaseException as error:
                result["error"] = error
            finally:
                finished.set()

        worker = threading.Thread(target=call)
        worker.start()
        try:
            while not finished.wait(0.005):
                self.server.poll()
        finally:
            worker.join(timeout=4)
        self.assertFalse(worker.is_alive(), "bounded mailbox client did not terminate")
        if "error" in result:
            raise result["error"]
        return result["value"]

    def test_changes_uses_frozen_pagination_without_consuming_notifications(self):
        comments = [self.forum.add_comment(self.topic["id"], "human", f"@reader update {number}")
                    for number in range(3)]
        first = self.exchange("changes", {"thread_id": self.topic["id"], "limit": 1})
        self.assertEqual([self.topic["event_id"]], [item["event_id"] for item in first["items"]])
        self.assertEqual("preview", first["content_format"])
        later = self.forum.add_comment(self.topic["id"], "human", "@reader arrived after the freeze")
        pending_before_read = self.forum.pending_notifications(self.peer["id"])
        activity_cursor = self.forum.get_agent(self.peer["id"])["last_activity_id"]
        event_ids = []
        cursor = first["next_cursor"]
        while cursor is not None:
            page = self.exchange("changes", {
                "thread_id": self.topic["id"], "after_event": cursor,
                "through_event": first["through_event"], "limit": 2,
            })
            self.assertEqual(first["through_event"], page["through_event"])
            event_ids.extend(item["event_id"] for item in page["items"])
            cursor = page["next_cursor"]
        self.assertEqual([comment["event_id"] for comment in comments], event_ids)
        self.assertNotIn(later["event_id"], event_ids)
        following = self.exchange("changes", {
            "thread_id": self.topic["id"], "after_event": first["through_event"],
        })
        self.assertEqual([later["event_id"]], [item["event_id"] for item in following["items"]])
        self.assertEqual(pending_before_read, self.forum.pending_notifications(self.peer["id"]))
        self.assertEqual(activity_cursor, self.forum.get_agent(self.peer["id"])["last_activity_id"])

    def test_reply_trigger_keeps_the_actual_event_parent_and_full_metadata_round_trip(self):
        trigger = self.forum.add_comment(self.topic["id"], "human", "@reader Check this specific claim")
        # A newer mention in another thread must not replace the explicitly
        # selected event, even though an implicit reply would resolve it.
        self.forum.add_comment(self.evidence["id"], "human", "@reader A different, later question")
        validation = "검증 명령: python3 -m unittest\n작성자 보고: 3개 통과"
        reply = self.exchange("reply-trigger", {
            "event": trigger["event_id"], "body": "Source-specific verification report",
            "relation": "verifies", "validation": validation,
            "evidence_event_ids": [self.evidence["event_id"]],
        })
        self.assertEqual("reader", reply["author"])
        self.assertEqual(self.topic["id"], reply["thread_id"])
        self.assertEqual(trigger["event_id"], reply["trigger_event_id"])
        self.assertEqual(trigger["event_id"], reply["provenance"]["reply_to_event_id"])
        self.assertEqual(trigger["id"], reply["provenance"]["reply_to_event"]["subject_id"])
        self.assertEqual("verifies", reply["provenance"]["relation"])
        self.assertEqual(validation, reply["provenance"]["validation"])
        self.assertEqual([self.evidence["event_id"]], reply["provenance"]["evidence_event_ids"])
        self.assertEqual(self.evidence["id"], reply["provenance"]["evidence_events"][0]["thread_id"])
        read = self.exchange("read", {"thread_id": self.topic["id"]})
        stored = next(comment for comment in read["comments"] if comment["id"] == reply["id"])
        self.assertEqual(reply["event_id"], stored["event_id"])
        self.assertEqual(reply["provenance"], stored["provenance"])
        changes = self.exchange("changes", {
            "thread_id": self.topic["id"], "after_event": trigger["event_id"],
            "through_event": reply["event_id"],
        })
        self.assertEqual([reply["event_id"]], [item["event_id"] for item in changes["items"]])
        self.assertEqual(validation, changes["items"][0]["provenance"]["validation"])
        self.assertNotIn("verified", changes["items"][0]["provenance"])

    def test_replayed_reply_request_survives_restart_without_duplicate_comment_or_links(self):
        request_id = uuid.uuid4().hex
        payload = {
            "thread_id": self.topic["id"], "body": "Independent supporting evidence",
            "reply_to_event_id": self.topic["event_id"], "relation": "supports",
            "validation": "Read the referenced source", "evidence_event_ids": [self.evidence["event_id"]],
        }
        first = self.exchange("reply", payload, request_id=request_id)
        self.assertEqual([], list((self.mailbox / "responses").iterdir()))
        self.server.close()
        self.server = self.new_server()
        self.server.register(self.peer["id"], self.workspace)
        repeated = self.exchange("reply", payload, request_id=request_id)
        self.assertEqual(first, repeated)
        self.assertEqual(1, self.dispatched.count("reply"))
        self.assertEqual([first["id"]], [comment["id"] for comment in self.forum.get_thread(self.topic["id"])["comments"]])
        with self.forum._connection() as connection:
            event_count = connection.execute("SELECT COUNT(*) FROM activity WHERE subject_id = ?", (first["id"],)).fetchone()[0]
            provenance_count = connection.execute("SELECT COUNT(*) FROM comment_provenance WHERE comment_id = ?", (first["id"],)).fetchone()[0]
            evidence_count = connection.execute("SELECT COUNT(*) FROM comment_evidence WHERE comment_id = ?", (first["id"],)).fetchone()[0]
        self.assertEqual((1, 1, 1), (event_count, provenance_count, evidence_count))
        with self.assertRaises(BridgeRemoteError) as conflict:
            self.exchange("reply", payload | {"body": "Different mutation"}, request_id=request_id)
        self.assertEqual("request_conflict", conflict.exception.code)
        self.assertEqual(1, self.dispatched.count("reply"))
        self.assertEqual(1, self.forum.get_thread(self.topic["id"])["comment_count"])

    def test_invalid_provenance_and_cross_run_changes_are_rejected_without_mutation(self):
        invalid = [
            ("reply-trigger", {"event": self.topic["event_id"], "body": "Wrong parent",
                               "reply_to_event_id": self.evidence["event_id"]}),
            ("reply", {"thread_id": self.topic["id"], "body": "Missing validation",
                       "reply_to_event_id": self.topic["event_id"], "relation": "verifies"}),
            ("reply", {"thread_id": self.topic["id"], "body": "Unowned retraction",
                       "reply_to_event_id": self.topic["event_id"], "relation": "retracts"}),
        ]
        for command, payload in invalid:
            with self.subTest(command=command, payload=payload), self.assertRaises(BridgeRemoteError):
                self.exchange(command, payload)
        self.assertEqual(0, self.forum.get_thread(self.topic["id"])["comment_count"])
        other = self.forum.create_run("Other run", self.root)
        other_thread = self.forum.create_thread(other["id"], "human", "Separate", "Different run")
        with self.assertRaises(BridgeRemoteError):
            self.exchange("changes", {"thread_id": other_thread["id"]})


if __name__ == "__main__":
    unittest.main()
