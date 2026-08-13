from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
import urllib.parse
import urllib.request
from http.cookiejar import CookieJar
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

from idea.forum import Forum
from idea.web import (
    DEFAULT_WEB_PASSWORD,
    PasswordSessions,
    _safe_next_path,
    make_server,
)


class WebForumTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.forum = Forum(self.root / ".idea")
        self.run = self.forum.create_run("goal <unsafe>", self.root)
        self.server = make_server(self.forum, "127.0.0.1", 0)
        self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.server_thread.start()
        host, port = self.server.server_address[:2]
        self.base_url = f"http://{host}:{port}"
        self.raw_opener = urllib.request.build_opener()
        self.cookies = CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.cookies)
        )
        self.login()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.server_thread.join(timeout=2)
        self.temp.cleanup()

    def get_text(self, path: str) -> str:
        with self.opener.open(f"{self.base_url}{path}", timeout=3) as response:
            return response.read().decode()

    def get_json(self, path: str) -> dict:
        return json.loads(self.get_text(path))

    def login(self, password: str = DEFAULT_WEB_PASSWORD, next_path: str = "/") -> str:
        data = urllib.parse.urlencode(
            {"password": password, "next": next_path}
        ).encode()
        request = urllib.request.Request(f"{self.base_url}/login", data=data)
        with self.opener.open(request, timeout=3) as response:
            response.read()
            return response.geturl()

    def test_login_protects_pages_and_api_and_preserves_destination(self) -> None:
        destination = f"/?run={self.run['id']}"
        with self.raw_opener.open(f"{self.base_url}{destination}", timeout=3) as response:
            login_page = response.read().decode()
            self.assertIn("/login?next=", response.geturl())
            self.assertIn("IDEA / FORUM", login_page)
            self.assertNotIn("goal &lt;unsafe&gt;", login_page)

        with self.assertRaises(HTTPError) as context:
            self.raw_opener.open(
                f"{self.base_url}/api/runs/{self.run['id']}", timeout=3
            )
        self.assertEqual(401, context.exception.code)
        self.assertEqual(
            {"error": "authentication required"},
            json.loads(context.exception.read()),
        )

        self.assertEqual(destination, self.login(next_path=destination).removeprefix(self.base_url))
        self.assertIn("data-idea-app", self.get_text(destination))

    def test_wrong_password_is_rejected_and_logout_revokes_session(self) -> None:
        other_cookies = CookieJar()
        other = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(other_cookies)
        )
        data = urllib.parse.urlencode(
            {"password": "wrong-password", "next": "/"}
        ).encode()
        with self.assertRaises(HTTPError) as context:
            other.open(
                urllib.request.Request(f"{self.base_url}/login", data=data),
                timeout=3,
            )
        self.assertEqual(401, context.exception.code)
        self.assertIn("패스워드가 올바르지 않습니다", context.exception.read().decode())
        self.assertEqual([], list(other_cookies))

        logout = urllib.request.Request(f"{self.base_url}/logout", data=b"")
        with self.opener.open(logout, timeout=3) as response:
            self.assertTrue(response.geturl().endswith("/login"))
        with self.assertRaises(HTTPError) as logged_out:
            self.opener.open(f"{self.base_url}/api/runs", timeout=3)
        self.assertEqual(401, logged_out.exception.code)

    def test_default_password_and_session_cookie_security_attributes(self) -> None:
        self.assertEqual("wwwlkwwwlk", DEFAULT_WEB_PASSWORD)
        auth = PasswordSessions(DEFAULT_WEB_PASSWORD, secure_cookie=True)
        self.assertTrue(auth.verify_password(DEFAULT_WEB_PASSWORD))
        self.assertFalse(auth.verify_password("not-it"))
        cookie = auth.session_cookie(auth.issue())
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Strict", cookie)
        self.assertIn("Secure", cookie)
        self.assertIn("Max-Age=43200", cookie)

    def test_environment_override_and_redirect_validation(self) -> None:
        with patch.dict(os.environ, {"IDEA_WEB_PASSWORD": "custom-password"}):
            other_forum = Forum(self.root / ".idea-other")
            other_server = make_server(other_forum, "127.0.0.1", 0)
        try:
            other_auth = other_server.RequestHandlerClass.auth
            self.assertTrue(other_auth.verify_password("custom-password"))
            self.assertFalse(other_auth.verify_password(DEFAULT_WEB_PASSWORD))
            self.assertNotEqual(
                self.server.RequestHandlerClass.auth.cookie_name,
                other_auth.cookie_name,
            )
        finally:
            other_server.server_close()

        self.assertEqual("/", _safe_next_path("//example.invalid/path"))
        self.assertEqual("/", _safe_next_path("/\\example.invalid/path"))
        self.assertEqual("/?run=run-id", _safe_next_path("/?run=run-id"))

    def test_page_is_a_lightweight_non_reloading_shell(self) -> None:
        marker = "BODY_MARKER_" + "x" * 20_000
        thread = self.forum.create_thread(self.run["id"], "peer", "hello", marker)

        page = self.get_text(f"/?run={self.run['id']}")

        self.assertIn("data-idea-app", page)
        self.assertIn("goal &lt;unsafe&gt;", page)
        self.assertNotIn('http-equiv="refresh"', page.lower())
        self.assertNotIn(marker, page)
        self.assertNotIn("<article class=\"thread\"", page)

        listing = self.get_json(f"/api/runs/{self.run['id']}/threads?limit=30")
        self.assertEqual("hello", listing["items"][0]["title"])
        self.assertNotIn("body", listing["items"][0])
        self.assertLessEqual(len(listing["items"][0]["preview"]), 240)

        loaded = self.get_json(f"/api/threads/{thread['id']}")
        self.assertEqual(marker, loaded["body"])

    def test_web_assets_render_mentions_as_safe_distinct_badges(self) -> None:
        page = self.get_text(f"/?run={self.run['id']}")

        self.assertIn("mention mention-${kind}", page)
        self.assertIn('badge.dataset.mentionKind = kind', page)
        self.assertIn('kind = "all"', page)
        self.assertIn('kind = "peer"', page)
        self.assertIn('kind = "unknown"', page)
        self.assertIn('kind = "human"', page)
        self.assertIn("document.createTextNode", page)
        self.assertIn(".mention-all", page)
        self.assertIn(".mention-human", page)
        self.assertIn(".mention-unknown", page)
        self.assertIn('alert.dataset.testid = "human-mention-alert"', page)
        self.assertIn("클릭해서 멘션 위치로 이동", page)

    def test_thread_listing_is_keyset_paginated_and_searchable(self) -> None:
        threads = [
            self.forum.create_thread(
                self.run["id"], "peer", f"thread {number:02d}", f"body {number}"
            )
            for number in range(45)
        ]
        self.forum.add_comment(threads[13]["id"], "reviewer", "unique-search-needle")

        first = self.get_json(f"/api/runs/{self.run['id']}/threads?limit=17")
        self.assertEqual(17, len(first["items"]))
        self.assertEqual(45, first["total_count"])
        self.assertIsNotNone(first["next_cursor"])

        cursor = urllib.parse.quote(first["next_cursor"])
        second = self.get_json(
            f"/api/runs/{self.run['id']}/threads?limit=17&before={cursor}"
        )
        first_ids = {item["id"] for item in first["items"]}
        second_ids = {item["id"] for item in second["items"]}
        self.assertEqual(17, len(second["items"]))
        self.assertTrue(first_ids.isdisjoint(second_ids))

        query = urllib.parse.quote("unique-search-needle")
        search = self.get_json(f"/api/runs/{self.run['id']}/threads?q={query}")
        self.assertEqual(1, search["total_count"])
        self.assertEqual(threads[13]["id"], search["items"][0]["id"])

    def test_incremental_updates_are_constant_size_and_include_public_state(self) -> None:
        first = self.forum.create_thread(self.run["id"], "peer", "first", "body")
        baseline = self.forum.activity_high_water(self.run["id"])
        quiet = self.get_json(f"/api/runs/{self.run['id']}/updates?after={baseline}")
        self.assertEqual(0, quiet["new_count"])
        self.assertNotIn("statistics", quiet)

        self.forum.add_comment(first["id"], "peer-2", "late result")
        update = self.get_json(f"/api/runs/{self.run['id']}/updates?after={baseline}")
        self.assertEqual(1, update["new_count"])
        self.assertGreater(update["high_water"], baseline)
        self.assertEqual(1, update["statistics"]["comment_count"])
        self.assertEqual([], update["agents"])

    def test_incremental_updates_include_clickable_human_mention_data(self) -> None:
        thread = self.forum.create_thread(
            self.run["id"], "peer-a", "decision needed", "initial analysis"
        )
        baseline = self.forum.activity_high_water(self.run["id"])
        comment = self.forum.add_comment(
            thread["id"], "sol-max", "evidence is ready; @human please review"
        )
        self.forum.add_comment(thread["id"], "human", "my own @human note")

        update = self.get_json(
            f"/api/runs/{self.run['id']}/updates"
            f"?after={baseline}&mentions_after={baseline}"
        )

        mentions = update["human_mentions"]
        self.assertEqual(1, len(mentions["items"]))
        self.assertEqual(comment["id"], mentions["items"][0]["subject_id"])
        self.assertEqual(thread["id"], mentions["items"][0]["thread_id"])
        self.assertEqual("decision needed", mentions["items"][0]["thread_title"])
        self.assertIn("@human", mentions["items"][0]["preview"])
        self.assertNotIn("content", mentions["items"][0])
        self.assertFalse(mentions["has_more"])

    def test_legacy_full_export_remains_available(self) -> None:
        self.forum.create_thread(self.run["id"], "peer", "hello", "world")
        payload = self.get_json(f"/api/runs/{self.run['id']}")
        self.assertEqual("hello", payload["threads"][0]["title"])


if __name__ == "__main__":
    unittest.main()
