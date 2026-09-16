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

from idea.domain import AgentProfile, Effort, ProcessState, Provider
from idea.forum import Forum
from idea.web import make_server


class AttentionWebTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.forum = Forum(self.root / ".idea")
        self.run = self.forum.create_run("Peer attention", self.root)
        self.server = make_server(self.forum, "127.0.0.1", 0, password="test-password")
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()
        host, port = self.server.server_address[:2]
        self.base_url = f"http://{host}:{port}"
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(CookieJar())
        )
        login = urllib.request.Request(
            self.base_url + "/login",
            data=urllib.parse.urlencode({"password": "test-password"}).encode(),
        )
        with self.opener.open(login, timeout=5) as response:
            response.read()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.worker.join(timeout=2)
        self.temp.cleanup()

    def text(self, path: str) -> str:
        with self.opener.open(self.base_url + path, timeout=5) as response:
            return response.read().decode()

    def json(self, path: str) -> dict:
        return json.loads(self.text(path))

    def register(self, number: int) -> dict:
        return self.forum.register_agent(
            self.run["id"],
            AgentProfile(f"peer-{number:04d}", Provider.OPENAI, "codex-test", Effort.HIGH),
        )

    def test_500_peer_shell_directory_and_compact_polling(self) -> None:
        first = self.register(0)
        updates = f"/api/runs/{self.run['id']}/updates?peers=none"
        small = self.text(updates)
        for number in range(1, 500):
            self.register(number)

        page = self.text(f"/?run={self.run['id']}")
        self.assertEqual(30, page.count('class="peer"'))
        self.assertIn("Peers 500", page)
        self.assertIn('id="peer-search-input"', page)
        self.assertIn('id="peer-load-more"', page)

        large = self.text(updates)
        self.assertLess(len(large), len(small) + 100)
        payload = json.loads(large)
        self.assertNotIn("agents", payload)
        self.assertEqual(500, payload["agent_summary"]["total_count"])
        self.assertEqual({"created": 500}, payload["agent_summary"]["states"])
        self.assertNotEqual(json.loads(small)["agent_summary"]["version"], payload["agent_summary"]["version"])

        seen: set[str] = set()
        cursor = None
        while True:
            query = urllib.parse.urlencode({"limit": 37, **({"after": cursor} if cursor else {})})
            peers = self.json(f"/api/runs/{self.run['id']}/agents?{query}")
            self.assertLessEqual(len(peers["items"]), 37)
            for peer in peers["items"]:
                self.assertNotIn(peer["id"], seen)
                self.assertNotIn("pid", peer)
                self.assertNotIn("session_id", peer)
                seen.add(peer["id"])
            cursor = peers["next_cursor"]
            if cursor is None:
                break
        self.assertEqual(500, len(seen))
        search = self.json(f"/api/runs/{self.run['id']}/agents?q=peer-0499")
        self.assertEqual(["peer-0499"], [peer["name"] for peer in search["items"]])

        self.forum.set_process_state(first["id"], ProcessState.RUNNING)
        state_change = self.json(updates)
        self.assertEqual(1, state_change["agent_summary"]["states"]["running"])
        self.assertNotEqual(payload["agent_summary"]["version"], state_change["agent_summary"]["version"])
        self.forum.create_thread(self.run["id"], "human", "Share", "@all please read")
        queued = self.json(updates)
        self.assertEqual(500, queued["notifications"]["pending_agents"])
        self.assertEqual(1, queued["agent_summary"]["states"]["running"])

    def test_paged_comments_and_direct_mention_target(self) -> None:
        thread = self.forum.create_thread(self.run["id"], "human", "Long discussion", "Start")
        comments = [
            self.forum.add_comment(thread["id"], "peer", f"Reply {number} @human")
            for number in range(75)
        ]
        detail = self.json(f"/api/threads/{thread['id']}?comments_limit=30")
        self.assertEqual(30, len(detail["comments"]))
        self.assertEqual(75, detail["comment_count"])
        seen = [item["id"] for item in detail["comments"]]
        cursor = detail["comments_next_cursor"]
        while cursor:
            query = urllib.parse.urlencode({"limit": 30, "after": cursor})
            page = self.json(f"/api/threads/{thread['id']}/comments?{query}")
            seen.extend(item["id"] for item in page["items"])
            cursor = page["next_cursor"]
        self.assertEqual([item["id"] for item in comments], seen)
        target = self.json(f"/api/threads/{thread['id']}/comments/{comments[-1]['id']}")
        self.assertEqual(comments[-1]["body"], target["body"])

        other = self.forum.create_thread(self.run["id"], "human", "Other", "Different")
        with self.assertRaises(HTTPError) as scoped:
            self.json(f"/api/threads/{other['id']}/comments/{comments[-1]['id']}")
        self.assertEqual(404, scoped.exception.code)
        full = self.json(f"/api/threads/{thread['id']}")
        self.assertEqual(75, len(full["comments"]))

    def test_new_endpoints_require_login_and_validate_page_size(self) -> None:
        thread = self.forum.create_thread(self.run["id"], "human", "Discussion", "Start")
        comment = self.forum.add_comment(thread["id"], "human", "Reply")
        paths = [
            f"/api/runs/{self.run['id']}/agents",
            f"/api/runs/{self.run['id']}/updates?peers=none",
            f"/api/threads/{thread['id']}?comments_limit=30",
            f"/api/threads/{thread['id']}/comments",
            f"/api/threads/{thread['id']}/comments/{comment['id']}",
        ]
        for path in paths:
            with self.subTest(path=path), self.assertRaises(HTTPError) as unauthorized:
                urllib.request.urlopen(self.base_url + path, timeout=5)
            self.assertEqual(401, unauthorized.exception.code)
        for path in (
            f"/api/runs/{self.run['id']}/agents?limit=101",
            f"/api/threads/{thread['id']}?comments_limit=0",
            f"/api/threads/{thread['id']}/comments?limit=1000",
        ):
            with self.subTest(path=path), self.assertRaises(HTTPError) as invalid:
                self.json(path)
            self.assertEqual(400, invalid.exception.code)


if __name__ == "__main__":
    unittest.main()
