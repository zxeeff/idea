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
from idea.population import PopulationPolicy, PopulationStore
from idea.web import make_server
from idea.workspaces import WorkspaceStore


class PopulationWebTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.workspace = self.root / "source"
        self.workspace.mkdir()
        (self.workspace / "proposal.txt").write_text("baseline\n")
        self.forum = Forum(self.root / "state")
        self.run = self.forum.create_run("Visible collaboration", self.workspace)
        self.agent = self.forum.register_agent(
            self.run["id"], AgentProfile("peer-alpha", Provider.OPENAI, "test", Effort.HIGH)
        )
        self.server = make_server(self.forum, "127.0.0.1", 0, password="population-web-test")
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))
        login = urllib.request.Request(
            self.base_url + "/login",
            data=urllib.parse.urlencode({"password": "population-web-test"}).encode(),
        )
        with self.opener.open(login, timeout=5) as response:
            response.read()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.worker.join(timeout=2)
        self.temp.cleanup()

    def json(self, path: str) -> dict:
        with self.opener.open(self.base_url + path, timeout=5) as response:
            return json.loads(response.read())

    def publish(self, count: int = 1) -> tuple[WorkspaceStore, list[dict]]:
        store = WorkspaceStore(self.forum, self.run["id"])
        copy = store.prepare(self.agent["id"])
        results = []
        for number in range(count):
            (copy / "proposal.txt").write_text(f"Change {number}\n")
            results.append(store.publish(self.agent["id"], note=f"Proposal {number}", validation="Author-reported check"))
        return store, results

    def test_legacy_run_has_unconfigured_summary_and_empty_readonly_directories(self) -> None:
        summary = self.json(f"/api/runs/{self.run['id']}/scaling")
        self.assertFalse(summary["population"]["configured"])
        self.assertIsNone(summary["workspaces"]["mode"])
        self.assertEqual(0, summary["workspaces"]["artifact_count"])
        for resource in ("calls", "artifacts"):
            self.assertEqual([], self.json(f"/api/runs/{self.run['id']}/{resource}")["items"])
        update = self.json(f"/api/runs/{self.run['id']}/updates?peers=none")
        self.assertEqual(summary, update["scaling"])

    def test_artifact_directory_is_paged_and_metadata_excludes_host_paths_and_patch(self) -> None:
        store, artifacts = self.publish(12)
        seen: set[str] = set()
        cursor = None
        while True:
            query = urllib.parse.urlencode({"limit": 5, **({"after": cursor} if cursor else {})})
            page = self.json(f"/api/runs/{self.run['id']}/artifacts?{query}")
            self.assertLessEqual(len(page["items"]), 5)
            for item in page["items"]:
                self.assertNotIn(item["id"], seen)
                seen.add(item["id"])
                self.assertNotIn("patch_path", item)
                self.assertNotIn("patch_base64", item)
                self.assertEqual("peer-alpha", item["author"])
                self.assertEqual(1, item["file_count"])
            cursor = page["next_cursor"]
            if cursor is None:
                break
        self.assertEqual({item["id"] for item in artifacts}, seen)
        detail = self.json(f"/api/runs/{self.run['id']}/artifacts/{artifacts[0]['id']}")
        self.assertEqual(["proposal.txt"], detail["files"])
        self.assertEqual("Author-reported check", detail["validation"])
        self.assertNotIn("patch_path", detail)
        self.assertNotIn("patch_base64", detail)
        self.assertEqual("baseline\n", (self.workspace / "proposal.txt").read_text())
        summary = self.json(f"/api/runs/{self.run['id']}/scaling")
        self.assertEqual("isolated", summary["workspaces"]["mode"])
        self.assertEqual(12, summary["workspaces"]["artifact_count"])
        self.assertEqual(store.summary()["base_revision"], summary["workspaces"]["base_revision"])

    def test_artifact_metadata_is_run_scoped_and_no_apply_endpoint_exists(self) -> None:
        _, artifacts = self.publish()
        other = self.forum.create_run("Separate run", self.workspace)
        with self.assertRaises(HTTPError) as scoped:
            self.json(f"/api/runs/{other['id']}/artifacts/{artifacts[0]['id']}")
        self.assertEqual(404, scoped.exception.code)
        for path in (
            f"/api/runs/{self.run['id']}/artifacts/{artifacts[0]['id']}/integrate",
            f"/api/runs/{self.run['id']}/templates",
        ):
            request = urllib.request.Request(self.base_url + path, data=b"{}", headers={"Content-Type": "application/json"})
            with self.assertRaises(HTTPError) as unavailable:
                self.opener.open(request, timeout=5)
            self.assertEqual(404, unavailable.exception.code)
        self.assertEqual("baseline\n", (self.workspace / "proposal.txt").read_text())

    def test_collaboration_endpoints_require_login_and_bounded_limits(self) -> None:
        for resource in ("scaling", "calls", "artifacts"):
            path = f"/api/runs/{self.run['id']}/{resource}"
            with self.subTest(resource=resource), self.assertRaises(HTTPError) as unauthorized:
                urllib.request.urlopen(self.base_url + path, timeout=5)
            self.assertEqual(401, unauthorized.exception.code)
        for resource in ("calls", "artifacts"):
            with self.assertRaises(HTTPError) as invalid:
                self.json(f"/api/runs/{self.run['id']}/{resource}?limit=101")
            self.assertEqual(400, invalid.exception.code)

    def test_real_participation_calls_are_paged_and_counters_include_policy(self) -> None:
        store = PopulationStore(self.forum, self.run["id"])
        store.configure(policy=PopulationPolicy(
            initial_agents=1, max_agents=5, birth_burst=1, max_births=8,
            max_invocations=9, participation_grace=0, max_open_calls_per_agent=12,
        ))
        thread = self.forum.create_thread(self.run["id"], "peer-alpha", "Independent investigation", "Context")
        calls = [
            store.open_call(self.agent["id"], thread["id"], f"Approach {number}: " + "x" * 400)
            for number in range(12)
        ]
        seen: list[str] = []
        cursor = None
        while True:
            query = urllib.parse.urlencode({"limit": 5, **({"after": cursor} if cursor else {})})
            page = self.json(f"/api/runs/{self.run['id']}/calls?{query}")
            self.assertLessEqual(len(page["items"]), 5)
            for item in page["items"]:
                self.assertEqual(thread["id"], item["thread_id"])
                self.assertLessEqual(len(item["reason"]), 320)
                self.assertNotIn("request_key", item)
                self.assertNotIn("birth_id", item)
                seen.append(item["id"])
            cursor = page["next_cursor"]
            if cursor is None:
                break
        self.assertEqual([item["id"] for item in calls], seen)
        summary = self.json(f"/api/runs/{self.run['id']}/scaling")["population"]
        self.assertTrue(summary["configured"])
        self.assertEqual(12, summary["open_calls"])
        self.assertEqual(12, summary["ready_calls"])
        self.assertEqual(5, summary["policy"]["max_agents"])
        self.assertEqual(8, summary["policy"]["max_births"])
        self.assertEqual(9, summary["policy"]["max_invocations"])

        other = self.forum.register_agent(self.run["id"], AgentProfile("peer-beta", Provider.ANTHROPIC, "test", Effort.HIGH))
        store.volunteer(other["id"], calls[0]["id"])
        connected = self.json(f"/api/runs/{self.run['id']}/calls?state=filled")
        self.assertEqual([calls[0]["id"]], [item["id"] for item in connected["items"]])
        self.assertEqual("filled", connected["items"][0]["state"])
        with self.assertRaises(HTTPError) as invalid:
            self.json(f"/api/runs/{self.run['id']}/calls?state=finished")
        self.assertEqual(400, invalid.exception.code)

    def test_human_discussion_hides_coordination_protocol_comments(self) -> None:
        store = PopulationStore(self.forum, self.run["id"])
        store.configure(policy=PopulationPolicy(initial_agents=1, max_agents=5))
        thread = self.forum.create_thread(
            self.run["id"], "peer-alpha", "Separate protocol from discussion", "Context"
        )
        before_coordination = self.forum.activity_high_water(self.run["id"])
        call = store.open_call(
            self.agent["id"], thread["id"], "Investigate an independent branch"
        )
        internal = self.forum.add_comment(
            thread["id"], "system", "Run internal coordination command"
        )
        discussion = self.forum.add_comment(
            thread["id"], "peer-alpha", "Visible research result"
        )

        # Simulate an existing database whose participation message predates
        # presentation metadata, then reopen it to exercise the migration.
        with self.forum._connection() as connection:
            connection.execute(
                "DELETE FROM comment_presentation WHERE comment_id != ?", (internal["id"],)
            )
        Forum(self.forum.state_dir)

        agent_view = self.forum.get_thread(thread["id"])
        self.assertEqual(3, len(agent_view["comments"]))
        self.assertIn(call["id"], agent_view["comments"][0]["body"])

        human_view = self.json(f"/api/threads/{thread['id']}")
        self.assertEqual(1, human_view["comment_count"])
        self.assertEqual([discussion["id"]], [item["id"] for item in human_view["comments"]])
        paged = self.json(f"/api/threads/{thread['id']}/comments?limit=1")
        self.assertEqual([discussion["id"]], [item["id"] for item in paged["items"]])
        self.assertIsNone(paged["next_cursor"])

        raw_search = urllib.parse.urlencode({"q": "Open participation request"})
        self.assertEqual(
            0,
            self.json(f"/api/runs/{self.run['id']}/threads?{raw_search}")["total_count"],
        )
        visible_search = urllib.parse.urlencode({"q": "Visible research result"})
        visible = self.json(f"/api/runs/{self.run['id']}/threads?{visible_search}")
        self.assertEqual(1, visible["total_count"])
        self.assertEqual(1, visible["items"][0]["comment_count"])
        self.assertEqual(
            1,
            self.json(f"/api/runs/{self.run['id']}/overview")["statistics"]["comment_count"],
        )
        updates = self.json(
            f"/api/runs/{self.run['id']}/updates?after={before_coordination}&peers=none"
        )
        self.assertEqual(1, updates["new_count"])
        self.assertEqual(self.forum.activity_high_water(self.run["id"]), updates["high_water"])
        self.assertEqual(
            [call["id"]],
            [item["id"] for item in self.json(
                f"/api/runs/{self.run['id']}/calls"
            )["items"]],
        )
        with self.assertRaises(HTTPError) as hidden:
            self.json(f"/api/threads/{thread['id']}/comments/{internal['id']}")
        self.assertEqual(404, hidden.exception.code)

    def test_parked_peers_keep_process_state_and_are_counted_separately(self) -> None:
        others = {}
        for name, process_state in (
            ("failed-peer", ProcessState.FAILED),
            ("blocked-peer", ProcessState.BLOCKED),
            ("running-peer", ProcessState.RUNNING),
            ("retired-peer", ProcessState.RETIRED),
        ):
            peer = self.forum.register_agent(self.run["id"], AgentProfile(name, Provider.OPENAI, "test", Effort.HIGH))
            self.forum.set_process_state(peer["id"], process_state)
            others[name] = peer
        with self.forum._connection() as connection:
            for name in ("failed-peer", "blocked-peer", "retired-peer"):
                connection.execute(
                    "UPDATE agents SET participation_state='parked',parked_at=? WHERE id=?",
                    ("2026-09-15T00:00:00+00:00", others[name]["id"]),
                )
        store = PopulationStore(self.forum, self.run["id"])
        store.configure(policy=PopulationPolicy(initial_agents=1, max_agents=10))
        page = self.json(f"/api/runs/{self.run['id']}/agents?limit=10")
        records = {item["name"]: item for item in page["items"]}
        for name, original in (("failed-peer", "failed"), ("blocked-peer", "blocked")):
            self.assertEqual(original, records[name]["process_state"])
            self.assertEqual("parked", records[name]["participation_state"])
            self.assertIsNotNone(records[name]["parked_at"])
            self.assertNotIn("session_id", records[name])
        self.assertEqual(5, page["summary"]["total_count"])
        self.assertEqual(2, page["summary"]["resident_count"])
        self.assertEqual(2, page["summary"]["parked_count"])
        self.assertEqual(1, page["summary"]["states"]["running"])
        self.assertEqual(1, page["summary"]["states"]["failed"])

        summary = self.json(f"/api/runs/{self.run['id']}/scaling")["population"]
        self.assertEqual(2, summary["resident_agents"])
        self.assertEqual(2, summary["live_agents"])
        self.assertEqual(2, summary["parked_agents"])
        self.assertEqual(1, summary["running_agents"])
        self.assertEqual(1, summary["retired_agents"])
        self.assertEqual(5, summary["total_agents"])
        with self.opener.open(self.base_url + f"/?run={self.run['id']}", timeout=5) as response:
            html = response.read().decode()
        self.assertIn("Resident 2 · Parked 2", html)
        self.assertIn("Running 1 · Dormant 0", html)
        self.assertIn("Parked · 유휴 보존", html)
        self.assertIn("세션과 작업 사본을 보존한 유휴 상태", html)
        self.assertIn("state-failed", html)
        self.assertIn("state-blocked", html)

    def test_parking_change_updates_directory_version_without_new_activity(self) -> None:
        endpoint = f"/api/runs/{self.run['id']}/updates?peers=none"
        before = self.json(endpoint)
        with self.forum._connection() as connection:
            connection.execute("UPDATE agents SET participation_state='parked',parked_at=? WHERE id=?",
                               ("2026-09-15T00:00:00+00:00", self.agent["id"]))
        after = self.json(endpoint)
        self.assertNotEqual(before["agent_summary"]["version"], after["agent_summary"]["version"])
        self.assertEqual(before["high_water"], after["high_water"])
        self.assertEqual(0, after["agent_summary"]["resident_count"])
        self.assertEqual(1, after["agent_summary"]["parked_count"])
        self.assertEqual(before["agent_summary"]["states"], after["agent_summary"]["states"])
        self.assertNotIn("agents", after)

    def test_failed_preparation_call_remains_visible_without_becoming_open(self) -> None:
        store = PopulationStore(self.forum, self.run["id"])
        store.configure(policy=PopulationPolicy(initial_agents=1, max_agents=5))
        thread = self.forum.create_thread(self.run["id"], "peer-alpha", "Preparation failed", "Context")
        call = store.open_call(self.agent["id"], thread["id"], "Independent contribution")
        with self.forum._connection() as connection:
            connection.execute("UPDATE participation_calls SET state='failed' WHERE id=?", (call["id"],))
        for _ in range(2):
            failed = self.json(f"/api/runs/{self.run['id']}/calls?state=failed")
            self.assertEqual([call["id"]], [item["id"] for item in failed["items"]])
            self.assertEqual("failed", failed["items"][0]["state"])
            self.assertEqual([], self.json(f"/api/runs/{self.run['id']}/calls")["items"])
        self.assertEqual(1, len(self.forum.list_agents(self.run["id"])))


if __name__ == "__main__":
    unittest.main()
