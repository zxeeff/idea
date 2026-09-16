from __future__ import annotations

import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from idea.domain import AgentProfile, Effort, ProcessState, Provider
from idea.execution import ExecutionPolicy, ExecutionStore
from idea.forum import Forum
from idea.population import PopulationPolicy, PopulationStore


class PopulationAdmissionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.workspace = Path(temporary.name)
        self.forum = Forum(self.workspace / ".idea")
        self.run_id = self.forum.create_run("Independent evidence", self.workspace)["id"]
        self.thread = self.forum.create_thread(self.run_id, "human", "Evidence", "Explore independently")
        self.store = PopulationStore(self.forum, self.run_id)
        self.profiles = (
            AgentProfile("codex", Provider.OPENAI, "codex-test", Effort.HIGH),
            AgentProfile("claude", Provider.ANTHROPIC, "claude-test", Effort.HIGH),
        )
        self.policy = PopulationPolicy(initial_agents=2, max_agents=8, birth_burst=8,
            births_per_minute=60, max_births=40, max_invocations=100,
            participation_grace=0, call_ttl=1000, idle_timeout=30)
        clock = patch("idea.population.time.time", return_value=1_700_000_000.0)
        self.clock = clock.start()
        self.addCleanup(clock.stop)

    def start(self, count=2, **changes):
        self.policy = replace(self.policy, initial_agents=count, **changes)
        self.store.configure(self.profiles, self.policy)
        self.agents = []
        for _ in range(count):
            birth = self.store.reserve_birth(initial=True)
            agent = birth["agent"]
            self.assertTrue(self.invoke(agent["id"], new_session=True))
            self.forum.set_process_state(agent["id"], ProcessState.DORMANT,
                                         session_id=f"session-{agent['id']}", exit_code=0)
            self.agents.append(agent)
        return self.agents

    def invoke(self, agent_id, *, new_session=False):
        with self.forum._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            return self.store.admit_invocation(connection, agent_id, new_session=new_session)

    def call(self, actor=None, **kwargs):
        return self.store.open_call((actor or self.agents[0])["id"], self.thread["id"],
                                   "Independent contribution; quoted @all is evidence", **kwargs)

    def offers(self):
        with self.forum._connection() as connection:
            return [dict(row) for row in connection.execute("SELECT * FROM participation_offers ORDER BY rowid")]

    def acknowledge(self, offer):
        execution = ExecutionStore(self.forum, self.run_id, population=self.store)
        request = execution.enqueue(offer["agent_id"])
        attempt = execution.claim(request, [offer["event_id"]], ExecutionPolicy())
        self.assertIsNotNone(attempt)
        execution.finish(request, attempt, exit_code=0)

    def stale(self, agent_id):
        timestamp = datetime.fromtimestamp(self.clock.return_value - 100, timezone.utc).isoformat()
        with self.forum._connection() as connection:
            connection.execute("UPDATE agents SET exited_at=? WHERE id=?", (timestamp, agent_id))

    def test_policy_validation_and_old_persisted_defaults(self):
        self.start()
        for changes in ({"max_open_calls_per_agent": 0}, {"max_open_calls_per_agent": 501},
                        {"max_offers_per_call": -1}, {"max_offers_per_call": True},
                        {"offer_cooldown": float("nan")}, {"offer_cooldown": -1},
                        {"idle_timeout": 0}, {"idle_timeout": float("inf")}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                replace(self.policy, **changes)
        self.assertEqual(0, replace(self.policy, max_offers_per_call=0, offer_cooldown=0).max_offers_per_call)
        old = asdict(self.policy)
        for field in ("max_open_calls_per_agent", "max_offers_per_call", "offer_cooldown", "idle_timeout"):
            old.pop(field)
        with self.forum._connection() as connection:
            connection.execute("UPDATE population_config SET policy_json=?", (json.dumps(old),))
        restored = PopulationStore(self.forum, self.run_id).policy()
        self.assertEqual((4, 2, 300, 300), (restored.max_open_calls_per_agent,
            restored.max_offers_per_call, restored.offer_cooldown, restored.idle_timeout))

    def test_per_requester_cap_is_atomic_and_identical_retry_survives_cap(self):
        self.start(max_open_calls_per_agent=2)
        first = self.call(request_key="stable")

        def create(index):
            try:
                return self.call(request_key=f"other-{index}")
            except ValueError:
                return None

        with ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(create, range(32)))
        self.assertEqual(1, sum(result is not None for result in results))
        self.assertEqual(first["id"], self.call(request_key="stable")["id"])
        self.store.cancel_call(self.agents[0]["id"], first["id"])
        self.assertEqual("open", self.call()["state"])

    def test_offer_reuses_session_even_when_fresh_session_budget_is_exhausted(self):
        actor, candidate, watcher = self.start(3, max_births=3)
        self.forum.subscribe(watcher["id"], self.thread["id"], wake=True)
        call = self.call()
        previous = self.store.summary()
        watched = self.forum.pending_notifications(watcher["id"])
        self.assertIsNone(self.store.reserve_birth())
        offer = self.offers()[0]
        self.assertEqual(candidate["id"], offer["agent_id"])
        event = self.forum.pending_notifications(candidate["id"])[0]
        self.assertEqual(("invitation", 3), (event["notification_reason"], event["priority"]))
        self.assertEqual(watched, self.forum.pending_notifications(watcher["id"]))
        comment = self.forum.get_thread(self.thread["id"])["comments"][-1]
        self.assertEqual("system", comment["author"])
        self.assertIn(call["id"], comment["body"])
        self.assertIn("Optional", comment["body"])
        current = self.store.summary()
        self.assertEqual((previous["total_births"], previous["invocations_started"]),
                         (current["total_births"], current["invocations_started"]))
        self.assertEqual(1, current["pending_offers"])

    def test_unacknowledged_offer_survives_restart_and_blocks_birth(self):
        self.start()
        call = self.call()
        self.store.reserve_birth()
        offer = self.offers()[0]
        self.clock.return_value += 500
        self.store = PopulationStore(Forum(self.forum.state_dir), self.run_id)
        self.assertIsNone(self.store.reserve_birth())
        self.assertEqual([offer], self.offers())
        self.assertEqual("open", self.store.get_call(call["id"])["state"])
        self.acknowledge(offer)
        birth = self.store.reserve_birth()
        self.assertEqual(call["id"], birth["call_id"])
        self.assertEqual("acknowledged", self.offers()[0]["state"])

    def test_failed_offer_is_withdrawn_without_acknowledging_human_mentions(self):
        actor, candidate = self.start()
        call = self.call()
        self.store.reserve_birth()
        offer = self.offers()[0]
        self.forum.create_thread(self.run_id, "human", "Correction", f"More evidence @{candidate['name']}")
        self.forum.set_process_state(candidate["id"], ProcessState.BLOCKED, exit_code=0)
        birth = self.store.reserve_birth()
        self.assertEqual(call["id"], birth["call_id"])
        self.assertEqual("failed", self.offers()[0]["state"])
        with self.forum._connection() as connection:
            delivery = connection.execute("SELECT * FROM notification_deliveries WHERE event_id=?", (offer["event_id"],)).fetchone()
        self.assertIsNone(delivery["acknowledged_at"])
        self.assertIsNotNone(delivery["withdrawn_at"])
        remaining = self.forum.pending_notifications(candidate["id"])
        self.assertEqual(["mention"], [item["notification_reason"] for item in remaining])
        self.assertEqual("blocked", self.forum.get_agent(candidate["id"])["process_state"])

    def test_expiry_and_voluntary_acceptance_have_distinct_offer_history(self):
        actor, candidate = self.start()
        call = self.call(ttl=10)
        self.store.reserve_birth()
        self.clock.return_value += 11
        self.assertIsNone(self.store.reserve_birth())
        self.assertEqual("expired", self.offers()[0]["state"])
        self.assertEqual([], self.forum.pending_notifications(candidate["id"]))
        self.clock.return_value += 300
        accepted = self.call()
        self.store.reserve_birth()
        self.store.volunteer(candidate["id"], accepted["id"])
        self.assertEqual("accepted", self.offers()[-1]["state"])
        self.assertEqual("volunteer", self.store.get_call(accepted["id"])["fulfillment_kind"])
        self.assertEqual(2, self.store.summary()["total_births"])

    def test_max_offers_and_distinct_candidates_bound_one_call(self):
        self.start(4, max_offers_per_call=2)
        call = self.call()
        for _ in range(2):
            self.assertIsNone(self.store.reserve_birth())
            self.acknowledge(self.offers()[-1])
        birth = self.store.reserve_birth()
        self.assertEqual(call["id"], birth["call_id"])
        self.assertEqual(2, len(self.offers()))
        self.assertEqual(2, len({offer["agent_id"] for offer in self.offers()}))

    def test_cooldown_and_available_subset_preserve_existing_session(self):
        actor, candidate = self.start(max_births=2)
        first = self.call()
        self.assertIsNone(self.store.reserve_birth(available_agent_ids=[actor["id"]]))
        self.assertEqual([], self.offers())
        self.store.reserve_birth(available_agent_ids=[candidate["id"]])
        self.acknowledge(self.offers()[0])
        self.store.cancel_call(actor["id"], first["id"])
        self.call()
        self.assertIsNone(self.store.reserve_birth())
        self.assertEqual(1, len(self.offers()))
        self.clock.return_value += self.policy.offer_cooldown
        self.store.reserve_birth()
        self.assertEqual(2, len(self.offers()))
        self.assertEqual(candidate["id"], self.offers()[-1]["agent_id"])

    def test_recent_requester_admission_prevents_one_requesters_fifo_flood(self):
        actor, other = self.start(max_offers_per_call=0)
        first = self.call()
        for _ in range(3):
            self.clock.return_value += 1
            self.call()
        self.clock.return_value += 1
        competing = self.call(other)
        self.assertEqual(first["id"], self.store.reserve_birth()["call_id"])
        self.assertEqual(competing["id"], self.store.reserve_birth()["call_id"])

    def test_provider_backlog_blocks_only_capacity_it_can_use(self):
        actor, candidate = self.start()
        execution = ExecutionStore(self.forum, self.run_id, population=self.store)
        policy = ExecutionPolicy(max_concurrent=2, max_codex=1, max_claude=1)
        request = execution.enqueue(actor["id"])
        self.assertIsNotNone(execution.claim(request, [], policy))
        queued = self.forum.register_agent(self.run_id,
            AgentProfile("waiting-codex", Provider.OPENAI, "codex-test", Effort.HIGH))
        execution.enqueue(queued["id"])
        templates = {item["provider"]: item["id"] for item in self.store.templates()}
        self.call(template_id=templates["openai"])
        self.clock.return_value += 1
        allowed = self.call(template_id=templates["anthropic"])
        self.store.reserve_birth(execution_policy=policy)
        self.assertEqual(allowed["id"], self.offers()[0]["call_id"])
        self.assertEqual(candidate["id"], self.offers()[0]["agent_id"])
        self.assertEqual(2, self.store.summary()["total_births"])

    def test_retired_but_running_process_still_occupies_execution_slot(self):
        actor, candidate = self.start()
        self.call()
        execution = ExecutionStore(self.forum, self.run_id, population=self.store)
        policy = ExecutionPolicy(max_concurrent=1)
        request = execution.enqueue(actor["id"])
        attempt = execution.claim(request, [], policy)
        self.assertIsNotNone(attempt)
        self.forum.retire_agent(actor["id"], "Provider has not exited yet")
        self.assertEqual(1, self.store.summary()["running_agents"])
        self.assertIsNone(self.store.reserve_birth(execution_policy=policy))
        self.assertEqual([], self.offers())
        self.assertEqual(2, self.store.summary()["total_births"])
        execution.finish(request, attempt, exit_code=0)
        self.assertEqual(0, self.store.summary()["running_agents"])
        self.store.reserve_birth(execution_policy=policy)
        self.assertEqual(candidate["id"], self.offers()[0]["agent_id"])

    def test_pending_offer_reserves_room_before_reactor_enqueues_it(self):
        self.start(3)
        self.call()
        self.call()
        policy = ExecutionPolicy(max_concurrent=1)
        with ThreadPoolExecutor(max_workers=8) as executor:
            list(executor.map(lambda _: self.store.reserve_birth(execution_policy=policy), range(16)))
        self.assertEqual(1, len(self.offers()))
        self.assertEqual(3, self.store.summary()["total_births"])

    def test_parking_preserves_failure_session_and_notifications_and_skips_queued(self):
        agents = self.start(4)
        self.forum.set_process_state(agents[1]["id"], ProcessState.FAILED, exit_code=1)
        self.forum.set_process_state(agents[2]["id"], ProcessState.BLOCKED, exit_code=0)
        for agent in agents:
            self.stale(agent["id"])
        execution = ExecutionStore(self.forum, self.run_id, population=self.store)
        execution.enqueue(agents[3]["id"])
        self.forum.create_thread(self.run_id, "human", "Evidence", f"Try later @{agents[1]['name']}")
        before = self.forum.get_agent(agents[1]["id"])
        notifications = self.forum.pending_notifications(agents[1]["id"])
        parked = self.store.park_idle_peers(now=self.clock.return_value)
        self.assertEqual({agent["id"] for agent in agents[:3]}, set(parked))
        after = self.forum.get_agent(agents[1]["id"])
        for field in ("session_id", "process_state", "exit_code", "exited_at"):
            self.assertEqual(before[field], after[field])
        self.assertEqual(notifications, self.forum.pending_notifications(agents[1]["id"]))
        status = self.store.summary()
        self.assertEqual((1, 1, 3, 4), (status["resident_agents"], status["live_agents"], status["parked_agents"], status["total_agents"]))
        self.assertEqual([], self.store.park_idle_peers(now=self.clock.return_value))

    def test_pending_offer_prevents_parking_and_subset_limits_parking(self):
        actor, candidate = self.start()
        self.call()
        self.store.reserve_birth()
        self.stale(actor["id"])
        self.stale(candidate["id"])
        self.assertEqual([], self.store.park_idle_peers(available_agent_ids=[candidate["id"]]))
        self.assertEqual([actor["id"]], self.store.park_idle_peers())

    def test_parked_reactivation_respects_resident_cap_without_resetting_session(self):
        actor, candidate = self.start(max_agents=2, max_offers_per_call=0)
        self.stale(candidate["id"])
        self.store.park_idle_peers(available_agent_ids=[candidate["id"]])
        self.call()
        replacement = self.store.reserve_birth()
        before = self.store.summary()
        self.assertFalse(self.invoke(candidate["id"]))
        self.assertEqual("parked", self.forum.get_agent(candidate["id"])["participation_state"])
        self.assertEqual(before["invocations_started"], self.store.summary()["invocations_started"])
        self.forum.retire_agent(replacement["agent"]["id"], "Free resident slot")
        self.assertTrue(self.invoke(candidate["id"]))
        restored = self.forum.get_agent(candidate["id"])
        self.assertEqual(("resident", None, "dormant", f"session-{candidate['id']}"),
            (restored["participation_state"], restored["parked_at"], restored["process_state"], restored["session_id"]))
        self.assertEqual(before["total_births"], self.store.summary()["total_births"])

    def test_failed_birth_is_visible_and_does_not_reopen_or_refund(self):
        self.start(max_offers_per_call=0)
        call = self.call(request_key="failure")
        birth = self.store.reserve_birth()
        before = self.store.summary()
        self.store.finish_birth(birth["birth_id"], succeeded=False, error="Copy failed")
        failed = self.store.list_calls(state="failed")["items"]
        self.assertEqual([call["id"]], [item["id"] for item in failed])
        self.assertEqual("failed", self.call(request_key="failure")["state"])
        self.assertIsNone(self.store.reserve_birth())
        after = self.store.summary()
        self.assertEqual((before["total_births"], before["birth_tokens"]),
                         (after["total_births"], after["birth_tokens"]))

    def test_requested_template_matches_provider_model_and_effort_for_reuse(self):
        actor, candidate = self.start(max_births=2)
        template = next(item for item in self.store.templates() if item["provider"] == "anthropic")
        self.call(template_id=template["id"])
        with self.forum._connection() as connection:
            connection.execute("UPDATE agents SET effort='low' WHERE id=?", (candidate["id"],))
        self.assertIsNone(self.store.reserve_birth())
        self.assertEqual([], self.offers())
        with self.forum._connection() as connection:
            connection.execute("UPDATE agents SET effort='high',model='different-model' WHERE id=?", (candidate["id"],))
        self.assertIsNone(self.store.reserve_birth())
        self.assertEqual([], self.offers())
        with self.forum._connection() as connection:
            connection.execute("UPDATE agents SET model=? WHERE id=?", (template["model"], candidate["id"]))
        self.store.reserve_birth()
        self.assertEqual(candidate["id"], self.offers()[0]["agent_id"])

    def test_parked_resume_budget_failure_preserves_blocked_state(self):
        actor, candidate = self.start(max_invocations=2)
        self.forum.set_process_state(candidate["id"], ProcessState.BLOCKED, exit_code=0)
        self.stale(candidate["id"])
        self.store.park_idle_peers(available_agent_ids=[candidate["id"]])
        before = self.forum.get_agent(candidate["id"])
        self.assertFalse(self.invoke(candidate["id"], new_session=True))
        self.assertEqual(before, self.forum.get_agent(candidate["id"]))
        self.store.configure(policy=replace(self.policy, max_invocations=3))
        self.assertTrue(self.invoke(candidate["id"], new_session=True))
        after = self.forum.get_agent(candidate["id"])
        self.assertEqual(("resident", "blocked", before["session_id"]),
                         (after["participation_state"], after["process_state"], after["session_id"]))
        self.assertEqual(3, self.store.summary()["total_births"])


if __name__ == "__main__":
    unittest.main()
