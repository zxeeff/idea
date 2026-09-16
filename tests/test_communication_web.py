from __future__ import annotations

import json
import tempfile
import threading
import unittest
import urllib.parse
import urllib.request
from http.cookiejar import CookieJar
from pathlib import Path
from urllib.error import HTTPError

from idea.domain import AgentProfile, Effort, Provider
from idea.forum import Forum
from idea.web import make_server
from idea.workspaces import WorkspaceStore


class CommunicationWebTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.workspace = self.root / "project"
        self.workspace.mkdir()
        (self.workspace / "sample.txt").write_text("before\n")
        self.forum = Forum(self.root / "state")
        self.run = self.forum.create_run("Trace references", self.workspace)
        self.thread = self.forum.create_thread(self.run["id"], "human", "Original claim", "Claim")
        self.server = make_server(self.forum, "127.0.0.1", 0, password="communication-test")
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))
        with self.opener.open(urllib.request.Request(self.base + "/login", data=urllib.parse.urlencode({"password": "communication-test"}).encode()), timeout=5) as response:
            response.read()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.worker.join(timeout=2)
        self.temp.cleanup()

    def get(self, path):
        with self.opener.open(self.base + path, timeout=5) as response:
            return json.loads(response.read())

    def post(self, values):
        request = urllib.request.Request(self.base + f"/api/threads/{self.thread['id']}/comments", data=json.dumps(values).encode(), headers={"Content-Type": "application/json"})
        with self.opener.open(request, timeout=5) as response:
            self.assertEqual(201, response.status)
            return json.loads(response.read())

    def test_reply_references_are_retained_in_all_comment_reads_and_safe_for_links(self):
        evidence = self.forum.add_comment(self.thread["id"], "peer", "Observed evidence")
        reply = self.post({"author": "human", "body": "I checked this", "reply_to_event_id": evidence["event_id"], "relation": "verifies", "validation": "Two local checks passed; fixture only", "evidence_event_ids": [self.thread["event_id"]]})
        self.assertIsInstance(reply["event_id"], int)
        metadata = reply["provenance"]
        self.assertEqual("verifies", metadata["relation"])
        self.assertEqual(evidence["id"], metadata["reply_to_event"]["subject_id"])
        self.assertEqual(self.thread["id"], metadata["evidence_events"][0]["subject_id"])
        self.assertEqual("Two local checks passed; fixture only", metadata["validation"])
        self.assertNotIn("run_id", reply)
        self.assertGreaterEqual(reply["activity_high_water"], reply["event_id"])
        for path in (f"/api/threads/{self.thread['id']}", f"/api/threads/{self.thread['id']}?comments_limit=30", f"/api/threads/{self.thread['id']}/comments", f"/api/threads/{self.thread['id']}/comments/{reply['id']}"):
            with self.subTest(path=path):
                result = self.get(path)
                comments = result.get("comments", result.get("items", [result]))
                found = next(item for item in comments if item["id"] == reply["id"])
                self.assertEqual(metadata, found["provenance"])
                self.assertEqual(reply["event_id"], found["event_id"])

    def test_invalid_reports_and_other_authors_retractions_make_no_writes(self):
        target = self.thread["event_id"]
        high_water = self.forum.activity_high_water(self.run["id"])
        for invalid in (
            {"relation": "verifies", "validation": "  "},
            {"relation": "retracts", "author": "another-author"},
            {"relation": "supersedes", "author": "another-author"},
            {"relation": "automatic-truth"},
            {"evidence_event_ids": [True]},
            {"reply_to_event_id": True},
        ):
            with self.subTest(invalid=invalid), self.assertRaises(HTTPError) as error:
                self.post({"author": "human", "body": "Report", "reply_to_event_id": target, **invalid})
            self.assertEqual(400, error.exception.code)
        self.assertEqual(high_water, self.forum.activity_high_water(self.run["id"]))
        own = self.post({"author": "human", "body": "Withdrawing my original claim", "reply_to_event_id": target, "relation": "retracts"})
        self.assertEqual("retracts", own["provenance"]["relation"])

    def test_cross_run_references_are_rejected_and_unauthenticated_posts_are_blocked(self):
        other_run = self.forum.create_run("Other", self.workspace)
        other = self.forum.create_thread(other_run["id"], "human", "Other claim", "Unrelated")
        for values in ({"reply_to_event_id": other["event_id"]}, {"evidence_event_ids": [other["event_id"]]}):
            with self.subTest(values=values), self.assertRaises(HTTPError) as error:
                self.post({"author": "human", "body": "Invalid", **values})
            self.assertEqual(400, error.exception.code)
        request = urllib.request.Request(self.base + f"/api/threads/{self.thread['id']}/comments", data=b'{"author":"human","body":"Invalid"}', headers={"Content-Type": "application/json"})
        with self.assertRaises(HTTPError) as error:
            urllib.request.urlopen(request, timeout=5)
        self.assertEqual(401, error.exception.code)
        self.assertEqual([], self.forum.get_thread(self.thread["id"])["comments"])

    def test_artifact_reference_exposes_identity_without_host_paths(self):
        agent = self.forum.register_agent(self.run["id"], AgentProfile("builder", Provider.OPENAI, "test", Effort.LOW))
        store = WorkspaceStore(self.forum, self.run["id"])
        workspace = store.prepare(agent["id"])
        (workspace / "sample.txt").write_text("after\n")
        artifact = store.publish(agent["id"], note="Change", validation="One check")
        reply = self.post({"author": "human", "body": "Review this patch", "reply_to_event_id": self.thread["event_id"], "relation": "supports", "artifact_id": artifact["id"]})
        metadata = reply["provenance"]
        self.assertEqual(artifact["id"], metadata["artifact_id"])
        self.assertEqual(artifact["patch_sha256"], metadata["artifact"]["patch_sha256"])
        self.assertNotIn("patch_path", json.dumps(metadata))
        self.assertNotIn(str(self.root), json.dumps(metadata))

    def test_paged_reference_target_can_be_fetched_outside_first_comment_page(self):
        comments = [self.forum.add_comment(self.thread["id"], "peer", f"Reply {number}") for number in range(35)]
        last = comments[-1]
        reply = self.post({"author": "human", "body": "About the last comment", "reply_to_event_id": last["event_id"]})
        first_page = self.get(f"/api/threads/{self.thread['id']}?comments_limit=30")
        self.assertEqual(30, len(first_page["comments"]))
        reference = reply["provenance"]["reply_to_event"]
        focused = self.get(f"/api/threads/{reference['thread_id']}/comments/{reference['subject_id']}")
        self.assertEqual(last["event_id"], focused["event_id"])
        self.assertEqual("Reply 34", focused["body"])


if __name__ == "__main__":
    unittest.main()
