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

from idea.approaches import ApproachStore
from idea.domain import AgentProfile, Effort, Provider
from idea.forum import Forum
from idea.web import make_server


class ApproachesWebTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.forum = Forum(self.root / "state")
        self.run = self.forum.create_run("Compare approaches", self.root)
        self.thread = self.forum.create_thread(self.run["id"], "human", "First approach", "Original source")
        self.server = make_server(self.forum, "127.0.0.1", 0, password="approaches-test")
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.api = f"/api/runs/{self.run['id']}"
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))
        with self.opener.open(urllib.request.Request(self.base + "/login", data=urllib.parse.urlencode({"password": "approaches-test"}).encode()), timeout=5) as response:
            response.read()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.worker.join(timeout=2)
        self.temp.cleanup()

    def get(self, path):
        with self.opener.open(self.base + path, timeout=5) as response:
            return json.loads(response.read())

    def post(self, path, values):
        request = urllib.request.Request(self.base + path, data=json.dumps(values).encode(), headers={"Content-Type": "application/json"})
        with self.opener.open(request, timeout=5) as response:
            self.assertEqual(201, response.status)
            return json.loads(response.read())

    def create(self, thread=None, hypothesis="First hypothesis", parent_id=None):
        return self.post(self.api + "/approaches", {"thread_id": (thread or self.thread)["id"], "hypothesis": hypothesis, "next_check": "Check original boundary conditions", "parent_id": parent_id})

    def report(self, approach, **values):
        return self.post(self.api + f"/approaches/{approach['id']}/reports", {
            "summary": "Result from sample", "conditions": "Applies to this sample only", "open_questions": "Does it generalize?",
            "source_event_ids": [self.thread["event_id"]], **values,
        })

    def test_human_can_create_without_assigning_or_impersonating_peers(self):
        peer = self.forum.register_agent(self.run["id"], AgentProfile("reader", Provider.OPENAI, "test", Effort.LOW))
        approach = self.create()
        self.assertEqual("human", approach["author"])
        self.assertEqual(0, approach["member_count"])
        self.assertEqual("First hypothesis", self.get(self.api + f"/approaches/{approach['id']}")["hypothesis"])
        self.assertEqual([], self.get(self.api + f"/approaches/{approach['id']}/members")["items"])
        for forbidden in ({"author": "reader"}, {"agent_id": peer["id"]}):
            with self.subTest(forbidden=forbidden), self.assertRaises(HTTPError) as error:
                self.post(self.api + "/approaches", {"thread_id": self.thread["id"], "hypothesis": "Impersonation", "next_check": "Must reject", **forbidden})
            self.assertEqual(400, error.exception.code)
        for action in ("join", "leave"):
            with self.assertRaises(HTTPError) as error:
                self.post(self.api + f"/approaches/{approach['id']}/{action}", {"agent_id": peer["id"]})
            self.assertEqual(404, error.exception.code)
        self.assertEqual(1, len(self.forum.list_agents(self.run["id"])))

    def test_directory_search_and_thread_filters_are_paged_and_bounded(self):
        first = self.create(hypothesis="Unique literal_% " + "가" * 2000)
        expected = {first["id"]}
        for number in range(14):
            thread = self.forum.create_thread(self.run["id"], "human", f"Thread {number}", "Details")
            expected.add(self.create(thread, hypothesis=f"Alternative {number}", parent_id=first["id"])["id"])
        cursor = None
        found = []
        while True:
            params = urllib.parse.urlencode({"limit": 4, **({"after": cursor} if cursor else {})})
            page = self.get(self.api + "/approaches?" + params)
            self.assertLessEqual(len(page["items"]), 4)
            for item in page["items"]:
                self.assertLessEqual(len(item["hypothesis"].encode()), 512)
                found.append(item["id"])
            cursor = page["next_cursor"]
            if not cursor:
                break
        self.assertEqual(expected, set(found))
        self.assertEqual(len(expected), len(found))
        literal = self.get(self.api + "/approaches?" + urllib.parse.urlencode({"q": "literal_%"}))
        self.assertEqual([first["id"]], [item["id"] for item in literal["items"]])
        thread_page = self.get(self.api + "/approaches?thread=" + self.thread["id"])
        self.assertEqual([first["id"]], [item["id"] for item in thread_page["items"]])
        self.assertEqual(first["hypothesis"], self.get(self.api + f"/approaches/{first['id']}")["hypothesis"])

    def test_member_pages_preserve_states_and_do_not_expose_sessions(self):
        approach = self.create()
        store = ApproachStore(self.forum, self.run["id"])
        for number in range(12):
            peer = self.forum.register_agent(self.run["id"], AgentProfile(f"peer-{number}", Provider.OPENAI, "test", Effort.LOW))
            store.join(approach["id"], peer["id"], focus=f"Check {number}")
        seen = set()
        cursor = None
        while True:
            page = self.get(self.api + f"/approaches/{approach['id']}/members?" + urllib.parse.urlencode({"limit": 5, **({"after": cursor} if cursor else {})}))
            self.assertLessEqual(len(page["items"]), 5)
            for member in page["items"]:
                self.assertNotIn(member["agent_id"], seen)
                seen.add(member["agent_id"])
                self.assertIn("process_state", member)
                self.assertNotIn("session_id", member)
                self.assertNotIn("pid", member)
            cursor = page["next_cursor"]
            if not cursor:
                break
        self.assertEqual(12, len(seen))

    def test_report_read_and_transfer_preserve_provenance_and_applicability(self):
        source = self.create()
        other = self.forum.create_thread(self.run["id"], "human", "Target", "A related question")
        target = self.create(other, hypothesis="Different hypothesis")
        report = self.report(source)
        detail = self.get(self.api + f"/reports/{report['id']}")
        self.assertEqual("Applies to this sample only", detail["conditions"])
        self.assertEqual(self.thread["id"], detail["source_events"][0]["subject_id"])
        for parameters in ({"approach": source["id"]}, {"thread": self.thread["id"]}, {"q": "sample"}):
            page = self.get(self.api + "/reports?" + urllib.parse.urlencode(parameters))
            self.assertEqual([report["id"]], [item["id"] for item in page["items"]])
        exchange = self.post(self.api + f"/reports/{report['id']}/adoptions", {"target_approach_id": target["id"], "application": "Repeat the sample check under the target conditions"})
        self.assertEqual(report["id"], exchange["source_report"]["id"])
        self.assertEqual(detail["source_event_ids"], exchange["source_report"]["source_event_ids"])
        comments = self.forum.get_thread(other["id"])["comments"]
        self.assertTrue(any("target conditions" in comment["body"] for comment in comments))
        public = self.get(f"/api/threads/{other['id']}?comments_limit=30")
        transferred = next(comment["exchange"] for comment in public["comments"] if comment.get("exchange"))
        self.assertEqual(report["id"], transferred["source_report"]["id"])
        source_page = self.get(f"/api/threads/{self.thread['id']}/comments")
        published = next(comment["report"] for comment in source_page["items"] if comment.get("report"))
        self.assertEqual(report["source_event_ids"], published["source_event_ids"])
        self.assertEqual(0, self.get(self.api + f"/approaches/{target['id']}")["member_count"])

    def test_invalid_report_and_adoption_make_no_writes(self):
        source = self.create()
        report = self.report(source)
        before = self.forum.activity_high_water(self.run["id"])
        for invalid in ({"source_event_ids": []}, {"conditions": " "}, {"validation_event_ids": [self.thread["event_id"]]}, {"author": "reader"}):
            with self.subTest(invalid=invalid), self.assertRaises(HTTPError) as error:
                self.report(source, **invalid)
            self.assertEqual(400, error.exception.code)
        with self.assertRaises(HTTPError) as error:
            self.post(self.api + f"/reports/{report['id']}/adoptions", {"target_approach_id": source["id"], "application": "Same approach"})
        self.assertEqual(400, error.exception.code)
        self.assertEqual(before, self.forum.activity_high_water(self.run["id"]))

    def test_new_endpoints_require_login_and_enforce_run_and_page_boundaries(self):
        approach = self.create()
        report = self.report(approach)
        paths = [self.api + "/approaches", self.api + f"/approaches/{approach['id']}", self.api + f"/approaches/{approach['id']}/members", self.api + "/reports", self.api + f"/reports/{report['id']}"]
        for path in paths:
            with self.subTest(path=path), self.assertRaises(HTTPError) as error:
                urllib.request.urlopen(self.base + path, timeout=5)
            self.assertEqual(401, error.exception.code)
        for path in (self.api + "/approaches?limit=101", self.api + "/reports?limit=0", self.api + f"/approaches/{approach['id']}/members?limit=999"):
            with self.assertRaises(HTTPError) as error:
                self.get(path)
            self.assertEqual(400, error.exception.code)
        other_run = self.forum.create_run("Other", self.root)
        for path in (f"/api/runs/{other_run['id']}/approaches/{approach['id']}", f"/api/runs/{other_run['id']}/reports/{report['id']}"):
            with self.assertRaises(HTTPError) as error:
                self.get(path)
            self.assertIn(error.exception.code, (400, 404))
        request = urllib.request.Request(self.base + self.api + "/approaches", data=b"{}", headers={"Content-Type": "application/json"})
        with self.assertRaises(HTTPError) as error:
            urllib.request.urlopen(request, timeout=5)
        self.assertEqual(401, error.exception.code)


if __name__ == "__main__":
    unittest.main()
