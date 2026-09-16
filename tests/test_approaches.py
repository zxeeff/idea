from __future__ import annotations

import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from idea.approaches import ApproachStore, MAX_DEFINITION_BYTES, MAX_FOCUS_BYTES, initialize
from idea.domain import AgentProfile, Effort, ProcessState, Provider
from idea.forum import Forum


class ApproachesTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.workspace = Path(directory.name)
        self.forum = Forum(self.workspace / ".idea")
        self.run_id = self.forum.create_run("Explore competing hypotheses", self.workspace)["id"]
        self.store = ApproachStore(self.forum, self.run_id)
        self.agent = self.peer("peer-a")
        self.thread = self.thread_named("Primary discussion")

    def peer(self, name, *, run_id=None):
        return self.forum.register_agent(
            run_id or self.run_id, AgentProfile(name, Provider.OPENAI, "codex", Effort.HIGH)
        )

    def thread_named(self, title, *, run_id=None):
        return self.forum.create_thread(run_id or self.run_id, "user", title, "Initial observations")

    def approach(self, *, thread=None, **kwargs):
        return self.store.create(
            (thread or self.thread)["id"], self.agent["name"],
            hypothesis=kwargs.pop("hypothesis", "Caching could reduce repeated work"),
            next_check=kwargs.pop("next_check", "Compare measured time on the same inputs"), **kwargs,
        )

    def counts(self):
        with self.forum._connection() as connection:
            return {name: connection.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
                    for name in ("agents", "comments", "activity", "notification_deliveries", "approaches", "approach_members")}

    def test_initialize_is_repeatable_and_reopening_preserves_public_history(self):
        approach = self.approach()
        self.store.join(approach["id"], self.agent["id"], focus="Measure latency", wake=True)
        before = self.counts()
        with self.forum._connection() as connection:
            initialize(connection)
            initialize(connection)
        reopened = ApproachStore(Forum(self.forum.state_dir), self.run_id)
        self.assertEqual(approach["event_id"], reopened.get(approach["id"])["event_id"])
        self.assertEqual(1, reopened.get(approach["id"])["member_count"])
        self.assertTrue(reopened.members(approach["id"])["items"][0]["wake"])
        self.assertEqual(before, self.counts())

    def test_create_exposes_one_public_definition_without_quoted_mention_fanout(self):
        watcher = self.peer("watcher")
        outsider = self.peer("outsider")
        self.forum.subscribe(watcher["id"], self.thread["id"], wake=True)
        before = self.counts()
        approach = self.approach(hypothesis="Quoted @all and @outsider are data")
        repeat = self.approach(hypothesis="Quoted @all and @outsider are data")
        self.assertEqual(approach, repeat)
        after = self.counts()
        self.assertEqual(before["activity"] + 1, after["activity"])
        self.assertEqual(before["comments"] + 1, after["comments"])
        self.assertEqual(before["agents"], after["agents"])
        self.assertEqual(0, approach["member_count"])
        comments = self.forum.get_thread(self.thread["id"])["comments"]
        self.assertIn(approach["id"], comments[-1]["body"])
        self.assertIn("Quoted @all and @outsider are data", comments[-1]["body"])
        self.assertEqual([], self.forum.pending_notifications(outsider["id"]))
        pending = self.forum.pending_notifications(watcher["id"])
        self.assertEqual([approach["event_id"]], [item["id"] for item in pending])
        self.assertEqual("subscription", pending[0]["notification_reason"])

    def test_definition_cannot_be_changed_and_derivation_uses_new_thread(self):
        parent = self.approach()
        before = self.counts()
        for changed in ({"hypothesis": "Alternative"}, {"next_check": "Different check"}, {"parent_id": parent["id"]}):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                self.approach(**changed)
        with self.assertRaises(ValueError):
            self.store.create(self.thread["id"], "another-author", hypothesis=parent["hypothesis"], next_check=parent["next_check"])
        self.assertEqual(before, self.counts())
        child = self.approach(thread=self.thread_named("Variant"), parent_id=parent["id"], hypothesis="Caching only hot keys")
        self.assertEqual(parent["id"], child["parent_id"])
        self.assertNotEqual(parent["thread_id"], child["thread_id"])
        self.assertEqual(parent["hypothesis"], self.store.get(parent["id"])["hypothesis"])

    def test_comment_and_notification_are_rolled_back_if_definition_insert_fails(self):
        watcher = self.peer("watcher")
        self.forum.subscribe(watcher["id"], self.thread["id"], wake=True)
        before = self.counts()
        with self.forum._connection() as connection:
            connection.execute("""CREATE TRIGGER reject_approach BEFORE INSERT ON approaches
                BEGIN SELECT RAISE(ABORT, 'simulated write failure'); END""")
        with self.assertRaises(sqlite3.IntegrityError):
            self.approach()
        self.assertEqual(before, self.counts())
        self.assertEqual([], self.forum.pending_notifications(watcher["id"]))

    def test_concurrent_create_and_join_do_not_duplicate_events_or_members(self):
        before = self.counts()
        with ThreadPoolExecutor(max_workers=8) as pool:
            approaches = list(pool.map(lambda _: self.approach(), range(16)))
        self.assertEqual(1, len({item["id"] for item in approaches}))
        approach_id = approaches[0]["id"]
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda _: self.store.join(approach_id, self.agent["id"]), range(16)))
        after = self.counts()
        self.assertEqual(before["activity"] + 1, after["activity"])
        self.assertEqual(1, after["approach_members"])
        self.assertEqual(1, self.store.get(approach_id)["member_count"])

    def test_same_run_scope_is_required_for_all_references(self):
        approach = self.approach()
        other_run = self.forum.create_run("Other goal", self.workspace)["id"]
        other_thread = self.thread_named("Other discussion", run_id=other_run)
        other_agent = self.peer("peer-a", run_id=other_run)
        other_store = ApproachStore(self.forum, other_run)
        other_approach = other_store.create(other_thread["id"], "user", hypothesis="h", next_check="n")
        fresh = self.thread_named("Fresh discussion")
        cases = (
            lambda: self.approach(thread=other_thread),
            lambda: self.approach(thread=fresh, parent_id=other_approach["id"]),
            lambda: self.store.get(other_approach["id"]),
            lambda: self.store.join(approach["id"], other_agent["id"]),
            lambda: self.store.join(other_approach["id"], self.agent["id"]),
            lambda: self.store.leave(approach["id"], other_agent["id"]),
            lambda: self.store.members(other_approach["id"]),
            lambda: self.store.list(agent_id=other_agent["id"]),
            lambda: self.store.list(thread_id=other_thread["id"]),
        )
        before = self.counts()
        for case in cases:
            with self.assertRaises(ValueError):
                case()
        self.assertEqual(before, self.counts())

    def test_join_is_voluntary_multiple_and_does_not_start_or_replay_work(self):
        first = self.approach()
        second = self.approach(thread=self.thread_named("Alternative"))
        self.forum.set_process_state(self.agent["id"], ProcessState.BLOCKED, session_id="keep-session")
        with self.forum._connection() as connection:
            connection.execute("UPDATE agents SET participation_state = 'parked', parked_at = ? WHERE id = ?", ("earlier", self.agent["id"]))
        before_agent = self.forum.get_agent(self.agent["id"])
        before = self.counts()
        first_member = self.store.join(first["id"], self.agent["id"], focus="Check consistency")
        second_member = self.store.join(second["id"], self.agent["id"], wake=True)
        self.assertFalse(first_member["wake"])
        self.assertTrue(second_member["wake"])
        self.assertEqual(before_agent, self.forum.get_agent(self.agent["id"]))
        self.assertEqual([], self.forum.pending_notifications(self.agent["id"]))
        after = self.counts()
        for name in ("agents", "activity", "comments", "notification_deliveries"):
            self.assertEqual(before[name], after[name])
        with self.forum._connection() as connection:
            self.assertEqual(0, connection.execute("SELECT COUNT(*) FROM thread_subscriptions").fetchone()[0])
        self.assertEqual({first["id"], second["id"]}, {item["id"] for item in self.store.list(agent_id=self.agent["id"])["items"]})

    def test_membership_updates_and_leave_preserve_explicit_subscription(self):
        approach = self.approach()
        self.forum.subscribe(self.agent["id"], self.thread["id"], wake=True)
        with patch("idea.approaches._now", return_value="2026-01-01T00:00:00+00:00"):
            first = self.store.join(approach["id"], self.agent["id"], focus="Measure")
        with patch("idea.approaches._now", return_value="2026-01-02T00:00:00+00:00"):
            updated = self.store.join(approach["id"], self.agent["id"], focus="Inspect results", wake=True)
        self.assertEqual(first["joined_at"], updated["joined_at"])
        self.assertEqual("Inspect results", updated["focus"])
        before = self.counts()
        left = self.store.leave(approach["id"], self.agent["id"])
        self.assertEqual(left, self.store.leave(approach["id"], self.agent["id"]))
        self.assertFalse(left["joined"])
        self.assertEqual([], self.store.members(approach["id"])["items"])
        self.assertEqual([], self.store.list(agent_id=self.agent["id"])["items"])
        with self.forum._connection() as connection:
            subscription = connection.execute("SELECT wake FROM thread_subscriptions WHERE agent_id = ? AND thread_id = ?", (self.agent["id"], self.thread["id"])).fetchone()
        self.assertEqual(1, subscription["wake"])
        self.assertEqual(before, self.counts())
        with patch("idea.approaches._now", return_value="2026-02-01T00:00:00+00:00"):
            rejoined = self.store.join(approach["id"], self.agent["id"])
        self.assertNotEqual(first["joined_at"], rejoined["joined_at"])
        self.assertTrue(rejoined["joined"])
        stranger = self.peer("stranger")
        never_joined = self.store.leave(approach["id"], stranger["id"])
        self.assertFalse(never_joined["joined"])
        self.assertIsNone(never_joined["joined_at"])

    def test_retired_agents_cannot_join_and_are_excluded_from_membership_counts(self):
        approach = self.approach()
        peers = [self.agent, self.peer("running"), self.peer("parked"), self.peer("left"), self.peer("retired")]
        for peer in peers:
            self.store.join(approach["id"], peer["id"])
        self.forum.set_process_state(peers[1]["id"], ProcessState.RUNNING)
        self.forum.set_process_state(peers[2]["id"], ProcessState.DORMANT)
        with self.forum._connection() as connection:
            connection.execute("UPDATE agents SET participation_state = 'parked' WHERE id = ?", (peers[2]["id"],))
        self.store.leave(approach["id"], peers[3]["id"])
        self.forum.retire_agent(peers[4]["id"], "Finished participation")
        with self.assertRaises(ValueError):
            self.store.join(approach["id"], peers[4]["id"])
        observed = self.store.get(approach["id"])
        self.assertEqual((3, 1, 1), tuple(observed[key] for key in ("member_count", "running_members", "parked_members")))
        self.assertEqual(3, len(self.store.members(approach["id"])["items"]))
        self.assertEqual([], self.store.list(agent_id=peers[4]["id"])["items"])
        self.assertFalse(self.store.leave(approach["id"], peers[4]["id"])["joined"])

    def test_search_is_literal_and_mine_and_thread_filters_are_independent(self):
        first = self.approach(hypothesis="A 10% improvement_in work")
        second = self.approach(thread=self.thread_named("A different experiment"))
        self.store.join(first["id"], self.agent["id"])
        self.assertEqual(2, len(self.store.list()["items"]))
        for query in ("10%", "_", "measured"):
            found = self.store.list(query=query)["items"]
            if query == "measured":
                self.assertEqual(2, len(found))
            else:
                self.assertEqual([first["id"]], [item["id"] for item in found])
        mine = self.store.list(agent_id=self.agent["id"])["items"]
        self.assertEqual([first["id"]], [item["id"] for item in mine])
        self.assertTrue(mine[0]["joined"])
        self.assertFalse(mine[0]["membership"]["wake"])
        self.assertEqual([second["id"]], [item["id"] for item in self.store.list(thread_id=second["thread_id"])["items"]])
        self.assertEqual([], self.store.list(thread_id=second["thread_id"], agent_id=self.agent["id"])["items"])

    def test_utf8_bounds_reject_oversized_input_and_previews_keep_full_definition(self):
        hypothesis = "가설🙂" * 1000
        next_check = "반복 검증 " * 1000
        approach = self.approach(hypothesis=hypothesis, next_check=next_check)
        listing = self.store.list()["items"][0]
        for key in ("hypothesis", "next_check"):
            self.assertLessEqual(len(listing[key].encode("utf-8")), 512)
            self.assertTrue(listing[f"{key}_truncated"])
            self.assertNotIn("�", listing[key])
        self.assertEqual(hypothesis, self.store.get(approach["id"])["hypothesis"])
        self.assertEqual(next_check.strip(), self.store.get(approach["id"])["next_check"])
        fresh = self.thread_named("Cannot create invalid definitions")
        before = self.counts()
        invalid_definitions = (
            {"hypothesis": "🙂" * (MAX_DEFINITION_BYTES // 4 + 1)},
            {"next_check": "x" * (MAX_DEFINITION_BYTES + 1)},
            {"hypothesis": " "}, {"next_check": []},
        )
        for invalid in invalid_definitions:
            with self.assertRaises(ValueError):
                self.approach(thread=fresh, **invalid)
        with self.assertRaises(ValueError):
            self.store.join(approach["id"], self.agent["id"], focus="🙂" * (MAX_FOCUS_BYTES // 4 + 1))
        self.assertEqual(before, self.counts())

    def test_member_profile_metadata_is_bounded_even_for_existing_long_names(self):
        approach = self.approach()
        peer = self.forum.register_agent(self.run_id, AgentProfile("p" * 4000, Provider.OPENAI, "모델" * 4000, Effort.HIGH))
        self.store.join(approach["id"], peer["id"])
        member = self.store.members(approach["id"])["items"][0]
        for key in ("name", "model"):
            self.assertLessEqual(len(member[key].encode("utf-8")), 1024)
            self.assertTrue(member[f"{key}_truncated"])
            self.assertNotIn("�", member[key])

    def test_public_arguments_reject_wrong_types_and_unknown_references(self):
        approach = self.approach()
        for invalid in (None, True, [], {}, 1):
            for action in (
                lambda: self.store.get(invalid),
                lambda: self.store.join(approach["id"], invalid),
                lambda: self.store.leave(invalid, self.agent["id"]),
                lambda: self.store.create(invalid, "user", hypothesis="h", next_check="n"),
            ):
                with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                    action()
        for invalid in (True, 0, 101, 1.5):
            with self.assertRaises(ValueError):
                self.store.list(limit=invalid)
            with self.assertRaises(ValueError):
                self.store.members(approach["id"], limit=invalid)
        with self.assertRaises(ValueError):
            self.store.list(after=[])
        with self.assertRaises(ValueError):
            self.store.join(approach["id"], self.agent["id"], wake=1)
        with self.assertRaises(KeyError):
            self.store.get("approach_missing")
        with self.assertRaises(KeyError):
            self.store.join(approach["id"], "agent_missing")

    def test_five_hundred_approaches_and_members_have_complete_bounded_keyset_pages(self):
        root = self.approach()
        timestamp = "2026-01-01T00:00:00+00:00"
        with self.forum._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            for number in range(500):
                thread_id = f"thread_fixture_{number:04d}"
                approach_id = f"approach_fixture_{number:04d}"
                agent_id = f"agent_fixture_{number:04d}"
                connection.execute("INSERT INTO threads(id,run_id,author,title,body,created_at) VALUES(?,?,?,?,?,?)",
                    (thread_id, self.run_id, "user", f"Variant {number}", "Evidence", timestamp))
                event = self.forum._add_comment(connection, thread_id, "user", "Hypothesis and next check", notification_text="")
                connection.execute("INSERT INTO approaches(id,run_id,thread_id,author,hypothesis,next_check,parent_id,event_id,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                    (approach_id, self.run_id, thread_id, "user", f"Variant {number}", "Measure", root["id"], event["event_id"], timestamp))
                connection.execute("INSERT INTO agents(id,run_id,name,provider,model,effort,process_state,created_at) VALUES(?,?,?,?,?,?,?,?)",
                    (agent_id, self.run_id, f"fixture-{number}", "openai", "codex", "high", "dormant", timestamp))
                connection.execute("INSERT INTO approach_members(approach_id,agent_id,focus,wake,joined_at) VALUES(?,?,?,?,?)",
                    (root["id"], agent_id, "Independent participation", 0, timestamp))
        approach_ids = []
        cursor = None
        while True:
            page = self.store.list(limit=37, after=cursor)
            self.assertLessEqual(len(page["items"]), 37)
            approach_ids.extend(item["id"] for item in page["items"])
            cursor = page["next_cursor"]
            if cursor is None:
                break
        self.assertEqual(501, len(approach_ids))
        self.assertEqual(sorted(set(approach_ids)), approach_ids)
        member_ids = []
        cursor = None
        while True:
            page = self.store.members(root["id"], limit=31, after=cursor)
            self.assertLessEqual(len(page["items"]), 31)
            member_ids.extend(item["agent_id"] for item in page["items"])
            cursor = page["next_cursor"]
            if cursor is None:
                break
        self.assertEqual(500, len(member_ids))
        self.assertEqual(sorted(set(member_ids)), member_ids)
        self.assertEqual(500, self.store.get(root["id"])["member_count"])


if __name__ == "__main__":
    unittest.main()
