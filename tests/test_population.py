from __future__ import annotations

import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from idea.domain import AgentProfile, Effort, ProcessState, Provider
from idea.forum import Forum
from idea.execution import ExecutionStore
from idea.population import PopulationPolicy, PopulationStore


class PopulationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.workspace = Path(self.directory.name)
        self.forum = Forum(self.workspace / ".idea")
        self.run = self.forum.create_run("Improve the shared document", self.workspace)
        self.run_id = str(self.run["id"])
        self.thread = self.forum.create_thread(self.run_id, "user", "Discussion", "Public evidence")
        self.store = PopulationStore(self.forum, self.run_id)
        self.profiles = (
            AgentProfile("a", Provider.OPENAI, "codex-a", Effort.LOW),
            AgentProfile("a-copy", Provider.OPENAI, "codex-a", Effort.LOW),
            AgentProfile("b", Provider.OPENAI, "codex-b", Effort.HIGH),
            AgentProfile("c", Provider.ANTHROPIC, "claude-a", Effort.HIGH),
        )
        self.policy = PopulationPolicy(initial_agents=2, max_agents=8, birth_burst=8,
            births_per_minute=60, max_births=40, max_invocations=100,
            participation_grace=10, call_ttl=100)
        self.clock_patch = patch("idea.population.time.time", return_value=1000.0)
        self.clock = self.clock_patch.start()
        self.addCleanup(self.clock_patch.stop)

    def configure(self, **changes):
        return self.store.configure(self.profiles, replace(self.policy, **changes))

    def seeds(self, count=2):
        return [self.store.reserve_birth(initial=True) for _ in range(count)]

    def call(self, agent_id, **kwargs):
        return self.store.open_call(agent_id, str(self.thread["id"]), "Consider an independent approach", **kwargs)

    def invoke(self, agent_id, *, new_session=True):
        with self.forum._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            return self.store.admit_invocation(connection, agent_id, new_session=new_session)

    def test_policy_rejects_invalid_and_nonfinite_limits(self) -> None:
        for arguments in (
            {"initial_agents": 0}, {"max_agents": 501}, {"birth_burst": True},
            {"max_births": 1.5}, {"max_invocations": 0}, {"births_per_minute": float("nan")},
            {"births_per_minute": 0}, {"participation_grace": -1}, {"call_ttl": float("inf")},
            {"initial_agents": 20, "max_agents": 10}, {"participation_grace": 100, "call_ttl": 100},
        ):
            with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                PopulationPolicy(**arguments)

    def test_configuration_deduplicates_templates_and_cycles_both_providers(self) -> None:
        self.configure(initial_agents=6)
        self.assertEqual(3, len(self.store.templates()))
        births = self.seeds(6)
        self.assertEqual(["openai", "anthropic"] * 3, [item["agent"]["provider"] for item in births])
        self.assertEqual(["codex-a", "codex-b", "codex-a"], [item["agent"]["model"] for item in births[::2]])
        self.assertEqual(6, len({item["agent"]["name"] for item in births}))
        self.assertIsNone(self.store.reserve_birth(initial=True))
        self.assertEqual(0, self.store.summary()["initial_remaining"])

    def test_reconfigure_preserves_tokens_counters_and_existing_snapshots(self) -> None:
        self.configure()
        first, second = self.seeds()
        before = self.store.summary()
        changed = (AgentProfile("new", Provider.OPENAI, "codex-replacement", Effort.HIGH),)
        self.store.configure(changed, replace(self.policy, birth_burst=16))
        after = self.store.summary()
        self.assertEqual(before["birth_tokens"], after["birth_tokens"])
        self.assertEqual(before["total_births"], after["total_births"])
        self.assertEqual("codex-a", self.forum.get_agent(first["agent"]["id"])["model"])
        self.assertEqual("claude-a", self.forum.get_agent(second["agent"]["id"])["model"])
        self.assertEqual(["codex-replacement"], [item["model"] for item in self.store.templates()])
        reopened = PopulationStore(Forum(self.forum.state_dir), self.run_id)
        reopened.configure()
        self.assertEqual(after["birth_tokens"], reopened.summary()["birth_tokens"])
        self.assertEqual(16, reopened.policy().birth_burst)

    def test_open_call_is_public_idempotent_and_does_not_broadcast(self) -> None:
        self.configure()
        first, second = self.seeds()
        actor = first["agent"]["id"]
        watcher = second["agent"]["id"]
        self.forum.subscribe(watcher, str(self.thread["id"]), wake=True)
        call = self.store.open_call(actor, str(self.thread["id"]), "Explore alternatives, even this quoted @all", request_key="same-call")
        retried = self.store.open_call(actor, str(self.thread["id"]), "Explore alternatives, even this quoted @all", request_key="same-call")
        self.assertEqual(call["id"], retried["id"])
        comments = self.forum.get_thread(str(self.thread["id"]))["comments"]
        self.assertEqual(1, len(comments))
        self.assertIn(call["id"], comments[0]["body"])
        self.assertIn("Explore alternatives", comments[0]["body"])
        self.assertEqual("subscription", self.forum.pending_notifications(watcher)[0]["notification_reason"])
        self.assertEqual([], self.forum.pending_notifications(actor))
        with self.assertRaises(ValueError):
            self.store.open_call(actor, str(self.thread["id"]), "Different request", request_key="same-call")

    def test_grace_volunteer_and_late_peer_join_never_spawn_twice(self) -> None:
        self.configure()
        first, second = self.seeds()
        call = self.call(first["agent"]["id"])
        self.assertIsNone(self.store.reserve_birth())
        filled = self.store.volunteer(second["agent"]["id"], call["id"])
        self.assertEqual("volunteer", filled["fulfillment_kind"])
        self.clock.return_value += 20
        self.assertIsNone(self.store.reserve_birth())
        another = self.call(first["agent"]["id"])
        self.clock.return_value += 10
        birth = self.store.reserve_birth()
        self.assertEqual(another["id"], birth["call_id"])
        self.assertEqual(another["thread_id"], birth["thread_id"])
        self.store.volunteer(second["agent"]["id"], another["id"])
        self.assertIsNone(self.store.reserve_birth())
        self.assertEqual("new_peer", self.store.get_call(another["id"])["fulfillment_kind"])

    def test_cancel_and_expiry_preserve_consumed_call_history(self) -> None:
        self.configure()
        first, second = self.seeds()
        actor = first["agent"]["id"]
        cancelled = self.call(actor)
        with self.assertRaises(ValueError):
            self.store.cancel_call(second["agent"]["id"], cancelled["id"])
        self.assertEqual("cancelled", self.store.cancel_call(actor, cancelled["id"])["state"])
        expiring = self.call(actor, ttl=5)
        self.clock.return_value += 6
        self.assertEqual("expired", self.store.get_call(expiring["id"])["state"])
        self.assertIsNone(self.store.reserve_birth())
        with self.assertRaises(ValueError):
            self.store.volunteer(second["agent"]["id"], expiring["id"])
        self.assertEqual([], self.store.list_calls()["items"])

    def test_retired_agents_cannot_request_or_volunteer_but_old_request_survives(self) -> None:
        self.configure(participation_grace=0)
        first, second = self.seeds()
        actor, other = first["agent"]["id"], second["agent"]["id"]
        call = self.call(actor)
        self.forum.retire_agent(actor, "Leave the invitation available")
        self.forum.retire_agent(other, "Done")
        with self.assertRaises(ValueError):
            self.call(actor)
        with self.assertRaises(ValueError):
            self.store.volunteer(other, call["id"])
        birth = self.store.reserve_birth()
        self.assertEqual(call["id"], birth["call_id"])
        self.assertEqual(1, self.store.summary()["live_agents"])

    def test_scope_and_allowed_template_checks(self) -> None:
        self.configure()
        first, second = self.seeds()
        other_run = self.forum.create_run("Other goal", self.workspace)
        other_thread = self.forum.create_thread(other_run["id"], "user", "Other", "Other run")
        other_agent = self.forum.register_agent(other_run["id"], self.profiles[0])
        for arguments in ((first["agent"]["id"], other_thread["id"]), (other_agent["id"], self.thread["id"])):
            with self.subTest(arguments=arguments), self.assertRaises((KeyError, ValueError)):
                self.store.open_call(*arguments, "Explore")
        with self.assertRaises(ValueError):
            self.call(first["agent"]["id"], template_id="unknown")
        claude = next(item for item in self.store.templates() if item["provider"] == "anthropic")
        call = self.call(first["agent"]["id"], template_id=claude["id"])
        self.clock.return_value += 10
        self.assertEqual("anthropic", self.store.reserve_birth()["agent"]["provider"])
        self.assertEqual("filled", self.store.get_call(call["id"])["state"])

    def test_capacity_counts_reserved_agents_and_failure_never_refunds_birth(self) -> None:
        self.configure(max_agents=3, participation_grace=0)
        first, second = self.seeds()
        call = self.call(first["agent"]["id"])
        birth = self.store.reserve_birth()
        self.call(first["agent"]["id"])
        self.assertIsNone(self.store.reserve_birth())
        before = self.store.summary()
        self.store.finish_birth(birth["birth_id"], succeeded=False, error="Cannot prepare workspace")
        after = self.store.summary()
        self.assertEqual(before["total_births"], after["total_births"])
        self.assertEqual(before["birth_tokens"], after["birth_tokens"])
        self.assertEqual(2, after["live_agents"])
        self.assertEqual("failed", self.store.get_call(call["id"])["state"])
        self.assertEqual("failed", self.store.birth_for_agent(birth["agent"]["id"])["state"])
        self.assertNotEqual(call["id"], self.store.reserve_birth()["call_id"])

    def test_crash_after_reservation_reuses_same_pending_birth(self) -> None:
        self.configure()
        first, second = self.seeds()
        self.store.finish_birth(first["birth_id"], succeeded=True)
        reopened = PopulationStore(Forum(self.forum.state_dir), self.run_id)
        self.assertEqual({first["birth_id"], second["birth_id"]}, {item["birth_id"] for item in reopened.pending_births()})
        self.assertIsNone(reopened.reserve_birth(initial=True))
        self.assertTrue(self.invoke(first["agent"]["id"]))
        self.assertEqual([second["birth_id"]], [item["birth_id"] for item in reopened.pending_births()])
        self.assertEqual(2, reopened.summary()["total_births"])

    def test_invocation_accounting_uses_prepaid_once_and_counts_fresh_restarts(self) -> None:
        self.configure(initial_agents=1, birth_burst=2, max_births=2, max_invocations=3)
        first = self.seeds(1)[0]
        actor = first["agent"]["id"]
        self.assertTrue(self.invoke(actor))
        self.assertEqual(1, self.store.summary()["total_births"])
        self.assertTrue(self.invoke(actor, new_session=False))
        self.assertEqual(1, self.store.summary()["total_births"])
        self.assertTrue(self.invoke(actor, new_session=True))
        self.assertEqual(2, self.store.summary()["total_births"])
        self.assertFalse(self.invoke(actor, new_session=False))
        self.assertEqual(3, self.store.summary()["invocations_started"])
        self.assertTrue(self.store.summary()["exhausted"])

    def test_birth_limit_does_not_block_resuming_existing_session(self) -> None:
        self.configure(initial_agents=1, max_births=1, max_invocations=4)
        actor = self.seeds(1)[0]["agent"]["id"]
        self.assertTrue(self.invoke(actor))
        self.assertFalse(self.invoke(actor, new_session=True))
        self.assertTrue(self.invoke(actor, new_session=False))
        status = self.store.summary()
        self.assertTrue(status["births_exhausted"])
        self.assertFalse(status["exhausted"])

    def test_fixed_mode_adopts_without_minting_prepaid_restarts(self) -> None:
        created = self.forum.register_agent(self.run_id, self.profiles[0])
        previous = self.forum.register_agent(self.run_id, self.profiles[3])
        self.forum.set_process_state(previous["id"], ProcessState.DORMANT, session_id="existing-session", exit_code=0)
        self.store.configure(self.profiles, replace(self.policy, birth_burst=1), enabled=False)
        self.assertFalse(self.store.enabled)
        self.assertIsNone(self.store.reserve_birth(initial=True))
        status = self.store.summary()
        self.assertEqual(2, status["adopted_agents"])
        self.assertEqual(1, status["adopted_known_starts"])
        self.assertEqual(1, status["total_births"])
        self.assertTrue(self.invoke(created["id"]))
        self.assertFalse(self.invoke(created["id"]))
        self.assertTrue(self.invoke(previous["id"], new_session=False))
        self.store.configure()
        self.assertEqual(2, self.store.summary()["total_births"])
        self.assertFalse(self.store.enabled)

    def test_more_initial_reservations_than_invocation_budget_are_not_created(self) -> None:
        self.configure(max_invocations=1)
        first = self.store.reserve_birth(initial=True)
        self.assertIsNotNone(first)
        self.assertIsNone(self.store.reserve_birth(initial=True))
        self.assertTrue(self.invoke(first["agent"]["id"]))
        self.assertTrue(self.store.summary()["exhausted"])

    def test_adoption_counts_existing_attempt_records_without_double_counting(self) -> None:
        agent = self.forum.register_agent(self.run_id, self.profiles[0])
        execution = ExecutionStore(self.forum, self.run_id)
        execution_policy = execution.configure(max_concurrent=1)
        for _ in range(3):
            request = execution.enqueue(agent["id"])
            attempt = execution.claim(request, [], execution_policy)
            self.assertIsNotNone(attempt)
            execution.finish(request, attempt, exit_code=0)
        self.store.configure(self.profiles, self.policy, enabled=False)
        summary = self.store.summary()
        self.assertEqual(3, summary["invocations_started"])
        self.assertEqual(1, summary["total_births"])
        self.store.configure()
        self.assertEqual(3, self.store.summary()["invocations_started"])

    def test_global_birth_bucket_does_not_multiply_with_500_requests(self) -> None:
        self.configure(initial_agents=1, max_agents=500, birth_burst=4,
                       births_per_minute=2, max_births=500, max_invocations=500,
                       participation_grace=0, max_open_calls_per_agent=500)
        actor = self.seeds(1)[0]["agent"]["id"]
        for index in range(500):
            self.call(actor, request_key=f"branch-{index}")
        admitted = []
        while birth := self.store.reserve_birth():
            admitted.append(birth)
        self.assertEqual(3, len(admitted))
        self.assertEqual(4, self.store.summary()["total_births"])
        self.assertEqual(497, self.store.summary()["open_calls"])
        self.assertFalse(self.store.summary()["births_exhausted"])
        self.assertEqual(30, self.store.summary()["next_birth_in_seconds"])
        self.clock.return_value += 30
        self.assertIsNotNone(self.store.reserve_birth())
        self.assertIsNone(self.store.reserve_birth())
        self.assertEqual(5, self.store.summary()["total_births"])

    def test_500_competing_new_session_attempts_share_one_budget(self) -> None:
        self.configure(initial_agents=1, birth_burst=4, max_births=4, max_invocations=3)
        actor = self.seeds(1)[0]["agent"]["id"]
        with ThreadPoolExecutor(max_workers=16) as executor:
            admitted = list(executor.map(lambda _: self.invoke(actor), range(500)))
        self.assertEqual(3, sum(admitted))
        status = self.store.summary()
        self.assertEqual(3, status["total_births"])
        self.assertEqual(3, status["invocations_started"])
        self.assertTrue(status["exhausted"])

    def test_volunteer_and_birth_race_have_at_most_one_new_member(self) -> None:
        self.configure(participation_grace=0)
        first, second = self.seeds()
        call = self.call(first["agent"]["id"])
        barrier = threading.Barrier(2)

        def reserve():
            barrier.wait()
            return self.store.reserve_birth()

        def volunteer():
            barrier.wait()
            return self.store.volunteer(second["agent"]["id"], call["id"])

        with ThreadPoolExecutor(max_workers=2) as executor:
            reserved, joined = executor.submit(reserve), executor.submit(volunteer)
            birth, result = reserved.result(), joined.result()
        filled = self.store.get_call(call["id"])
        self.assertEqual("filled", result["state"])
        self.assertEqual(2 + int(birth is not None), self.store.summary()["live_agents"])
        self.assertEqual("new_peer" if birth else "volunteer", filled["fulfillment_kind"])
        self.assertIsNone(self.store.reserve_birth())

    def test_cancellation_and_birth_race_cannot_refund_or_duplicate_admission(self) -> None:
        self.configure(participation_grace=0)
        first, second = self.seeds()
        call = self.call(first["agent"]["id"])
        barrier = threading.Barrier(2)

        def reserve():
            barrier.wait()
            return self.store.reserve_birth()

        def cancel():
            barrier.wait()
            try:
                return self.store.cancel_call(first["agent"]["id"], call["id"])
            except ValueError:
                return None

        with ThreadPoolExecutor(max_workers=2) as executor:
            reserved, cancelled = executor.submit(reserve), executor.submit(cancel)
            birth, result = reserved.result(), cancelled.result()
        self.assertEqual("filled" if birth else "cancelled", self.store.get_call(call["id"])["state"])
        self.assertEqual(2 + int(birth is not None), self.store.summary()["total_births"])
        self.assertIsNone(self.store.reserve_birth())

    def test_call_pages_are_stable_and_request_key_does_not_reopen_expired_call(self) -> None:
        self.configure(max_open_calls_per_agent=5)
        actor = self.seeds()[0]["agent"]["id"]
        calls = [self.call(actor, request_key=f"request-{index}") for index in range(5)]
        first = self.store.list_calls(limit=2)
        second = self.store.list_calls(limit=2, after=first["next_cursor"])
        third = self.store.list_calls(limit=2, after=second["next_cursor"])
        ids = [item["id"] for page in (first, second, third) for item in page["items"]]
        self.assertEqual({item["id"] for item in calls}, set(ids))
        self.assertEqual(5, len(ids))
        self.assertIsNone(third["next_cursor"])
        self.clock.return_value += 101
        self.assertEqual([], self.store.list_calls()["items"])
        retried = self.call(actor, request_key="request-0")
        self.assertEqual(calls[0]["id"], retried["id"])
        self.assertEqual("expired", retried["state"])


if __name__ == "__main__":
    unittest.main()
