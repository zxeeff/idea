from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from unittest.mock import patch

from idea.domain import AgentProfile, Effort, ProcessState, Provider
from idea.execution import ExecutionStore, RunLock
from idea.forum import Forum
from idea.launcher import prepare_resume, prepare_run, run_reactor


class AttentionRuntimeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.workspace = Path(self.directory.name).resolve()
        self.forum = Forum(self.workspace / ".idea")

    def prepare(self, count: int = 1):
        profiles = tuple(
            AgentProfile(
                f"peer-{index}",
                Provider.OPENAI if index % 2 == 0 else Provider.ANTHROPIC,
                "fake-codex" if index % 2 == 0 else "fake-claude",
                Effort.LOW,
            )
            for index in range(count)
        )
        return prepare_run(
            forum=self.forum, goal="Improve the shared document", workspace=self.workspace,
            profiles=profiles,
        )

    async def until(self, predicate, timeout: float = 3.0) -> None:
        deadline = asyncio.get_running_loop().time() + timeout
        while not predicate():
            if asyncio.get_running_loop().time() >= deadline:
                raise TimeoutError("Expected runtime state did not arrive")
            await asyncio.sleep(0.002)

    def test_500_peer_broadcast_respects_caps_and_holds_retiring_processes(self) -> None:
        prepared = self.prepare(500)
        run_id = str(prepared.run["id"])
        for index in range(3):
            self.forum.create_thread(run_id, "user", f"Broadcast {index}", "Review this update @all")
        calls: Counter[str] = Counter()
        active: Counter[str] = Counter()
        providers: Counter[str] = Counter()
        peaks: Counter[str] = Counter()
        duplicate_agents: list[str] = []
        pending_before_exit: list[int] = []
        inherited_descriptors: list[int] = []

        async def fake_runner(**kwargs):
            agent_id = str(kwargs["agent"]["id"])
            provider = kwargs["profile"].provider.value
            calls[agent_id] += 1
            active[agent_id] += 1
            providers[provider] += 1
            if active[agent_id] > 1:
                duplicate_agents.append(agent_id)
            peaks["total"] = max(peaks["total"], sum(active.values()))
            peaks[provider] = max(peaks[provider], providers[provider])
            inherited_descriptors.append(len(kwargs["invocation"].inherited_fds))
            # A peer can retire before its actual provider invocation returns.
            self.forum.retire_agent(agent_id, "Finished reviewing the broadcasts")
            pending_before_exit.append(len(self.forum.pending_notifications(agent_id)))
            await asyncio.sleep(0.001)
            active[agent_id] -= 1
            providers[provider] -= 1
            return 0

        codes = asyncio.run(asyncio.wait_for(run_reactor(
            forum=self.forum, prepared=prepared, runner=fake_runner, poll_interval=0.001,
            max_concurrent=4, max_codex=3, max_claude=2,
        ), timeout=60))
        self.assertEqual([0] * 500, codes)
        self.assertEqual(500, len(calls))
        self.assertEqual({1}, set(calls.values()))
        self.assertEqual([], duplicate_agents)
        self.assertGreater(peaks["total"], 1)
        self.assertLessEqual(peaks["total"], 4)
        self.assertLessEqual(peaks["openai"], 3)
        self.assertLessEqual(peaks["anthropic"], 2)
        self.assertEqual({3}, set(pending_before_exit))
        self.assertEqual({1}, set(inherited_descriptors))
        for peer in prepared.peers:
            self.assertEqual([], self.forum.pending_notifications(str(peer.agent["id"])))

    def test_store_enforces_shared_provider_caps_and_deduplicates_requests(self) -> None:
        prepared = self.prepare(3)
        run_id = str(prepared.run["id"])
        store = ExecutionStore(self.forum, run_id)
        policy = store.configure(max_concurrent=2, max_codex=1, max_claude=1)
        ids = [str(peer.agent["id"]) for peer in prepared.peers]
        requests = [store.enqueue(identifier) for identifier in ids]
        self.assertEqual(requests[0], store.enqueue(ids[0]))
        self.assertEqual(3, len(store.queued()))
        first_attempt = store.claim(requests[0], [], policy)
        self.assertIsNotNone(first_attempt)
        self.assertEqual(requests[0], store.enqueue(ids[0]))
        self.assertIsNone(store.claim(requests[0], [], policy))
        self.assertIsNone(store.claim(requests[2], [], policy))
        second_attempt = store.claim(requests[1], [], policy)
        self.assertIsNotNone(second_attempt)
        self.assertIsNone(store.claim(requests[2], [], policy))
        store.finish(requests[0], str(first_attempt), exit_code=0)
        self.assertIsNotNone(store.claim(requests[2], [], policy))
        reopened = ExecutionStore(Forum(self.forum.state_dir), run_id).configure()
        self.assertEqual((2, 1, 1), (reopened.max_concurrent, reopened.max_codex, reopened.max_claude))

    def test_recovery_preserves_delivery_and_rejects_stale_attempt_acknowledgment(self) -> None:
        prepared = self.prepare()
        run_id = str(prepared.run["id"])
        agent_id = str(prepared.peers[0].agent["id"])
        self.forum.create_thread(run_id, "user", "Request", "Please inspect this @peer-0")
        event_id = int(self.forum.pending_notifications(agent_id)[0]["id"])
        store = ExecutionStore(self.forum, run_id)
        policy = store.configure(max_concurrent=1)
        request_id = store.enqueue(agent_id)
        attempt_id = store.claim(request_id, [event_id], policy)
        self.assertIsNotNone(attempt_id)
        self.assertEqual([event_id], [int(item["id"]) for item in self.forum.pending_notifications(agent_id)])
        recovered = ExecutionStore(Forum(self.forum.state_dir), run_id)
        with RunLock(self.forum.state_dir, run_id):
            recovered.recover()
        self.assertEqual(request_id, recovered.queued()[0]["id"])
        store.finish(request_id, str(attempt_id), exit_code=0)
        self.assertEqual([event_id], [int(item["id"]) for item in self.forum.pending_notifications(agent_id)])
        next_attempt = recovered.claim(request_id, [event_id], recovered.configure())
        self.assertIsNotNone(next_attempt)
        self.assertNotEqual(attempt_id, next_attempt)
        recovered.finish(request_id, str(next_attempt), exit_code=1, failure_watermark=event_id)
        self.assertEqual(event_id, recovered.held_through(agent_id))
        self.assertEqual([event_id], [int(item["id"]) for item in self.forum.pending_notifications(agent_id)])
        resumed_request = recovered.enqueue(agent_id, kind="start")
        resumed_attempt = recovered.claim(resumed_request, [event_id], recovered.configure())
        recovered.finish(resumed_request, str(resumed_attempt), exit_code=0)
        self.assertEqual([], self.forum.pending_notifications(agent_id))

    def test_failed_batch_waits_for_new_explicit_mention_even_when_subscriptions_arrive(self) -> None:
        self.assert_failure_gate(backlog=1)

    def test_failed_backlog_beyond_first_page_unlocks_on_fresh_mention(self) -> None:
        self.assert_failure_gate(backlog=25)

    def test_blocked_zero_exit_retains_delivery_until_a_new_explicit_mention(self) -> None:
        self.assert_failure_gate(
            backlog=1, failed_state=ProcessState.BLOCKED, failed_exit_code=0,
        )

    def assert_failure_gate(
        self, *, backlog: int, failed_state: ProcessState = ProcessState.FAILED,
        failed_exit_code: int = 1,
    ) -> None:
        prepared = self.prepare(2)
        run_id = str(prepared.run["id"])
        worker_id = str(prepared.peers[0].agent["id"])
        thread = self.forum.create_thread(run_id, "peer-1", "Following", "Shared discussion")
        self.forum.subscribe(worker_id, str(thread["id"]), wake=True)
        for index in range(backlog):
            self.forum.add_comment(str(thread["id"]), "user", f"Original request {index} @peer-0")
        original_ids = [int(item["id"]) for item in self.forum.pending_notifications(worker_id, limit=100)]
        calls = Counter()
        observed: dict[str, object] = {}
        store = ExecutionStore(self.forum, run_id)
        fresh_event_id: int | None = None

        async def fake_runner(**kwargs):
            nonlocal fresh_event_id
            agent_id = str(kwargs["agent"]["id"])
            name = kwargs["profile"].name
            calls[name] += 1
            self.forum.set_process_state(agent_id, ProcessState.RUNNING)
            if name == "peer-0":
                if calls[name] == 1:
                    self.forum.set_process_state(agent_id, failed_state, exit_code=failed_exit_code)
                    return failed_exit_code
                trigger = kwargs["invocation"].env.get("IDEA_TRIGGER_EVENT_ID")
                if fresh_event_id is not None and trigger == str(fresh_event_id):
                    observed["fresh_received"] = True
                self.forum.set_process_state(agent_id, ProcessState.DORMANT, exit_code=0)
                return 0
            try:
                await self.until(lambda: store.held_through(worker_id) >= original_ids[-1])
                await asyncio.sleep(0.03)
                observed["quiet_calls"] = calls["peer-0"]
                observed["failed_pending"] = [int(item["id"]) for item in self.forum.pending_notifications(worker_id, limit=100)]
                failed_record = self.forum.get_agent(worker_id)
                observed["failed_state"] = failed_record["process_state"]
                observed["failed_exit_code"] = failed_record["exit_code"]
                with self.forum._connection() as connection:
                    failed_attempt = connection.execute(
                        """SELECT r.exit_code, t.exit_code FROM execution_requests r
                           JOIN execution_attempts t ON t.request_id=r.id
                           WHERE r.agent_id=? ORDER BY r.id DESC LIMIT 1""",
                        (worker_id,),
                    ).fetchone()
                observed["persisted_exit_codes"] = tuple(failed_attempt)
                self.forum.add_comment(str(thread["id"]), "peer-1", "A passive subscription update")
                await asyncio.sleep(0.05)
                observed["subscription_calls"] = calls["peer-0"]
                self.forum.add_comment(str(thread["id"]), "user", "Please retry now @peer-0")
                fresh_event_id = self.forum.activity_high_water(run_id)
                await self.until(lambda: bool(observed.get("fresh_received")))
                await self.until(lambda: not self.forum.pending_notifications(worker_id))
            finally:
                self.forum.retire_agent(agent_id, "Finished directing test events")
                self.forum.retire_agent(worker_id, "Test complete")
            return 0

        asyncio.run(asyncio.wait_for(run_reactor(
            forum=self.forum, prepared=prepared, runner=fake_runner,
            poll_interval=0.002, max_concurrent=2,
        ), timeout=6))
        self.assertEqual(1, observed.get("quiet_calls"))
        self.assertEqual(original_ids, observed.get("failed_pending"))
        self.assertEqual(failed_state.value, observed.get("failed_state"))
        self.assertEqual(failed_exit_code, observed.get("failed_exit_code"))
        self.assertEqual((failed_exit_code, failed_exit_code), observed.get("persisted_exit_codes"))
        self.assertEqual(1, observed.get("subscription_calls"))
        self.assertTrue(observed.get("fresh_received"))
        self.assertEqual(1 + (backlog + 2 + 19) // 20, calls["peer-0"])
        self.assertEqual([], self.forum.pending_notifications(worker_id))

    def test_mention_arriving_during_failed_invocation_can_retry_it(self) -> None:
        prepared = self.prepare(2)
        run_id = str(prepared.run["id"])
        worker_id = str(prepared.peers[0].agent["id"])
        thread = self.forum.create_thread(run_id, "user", "Initial request", "Please review @peer-0")
        calls = Counter()
        observed: dict[str, object] = {}

        async def scenario():
            worker_started = asyncio.Event()
            allow_failure = asyncio.Event()

            async def fake_runner(**kwargs):
                agent_id = str(kwargs["agent"]["id"])
                name = kwargs["profile"].name
                calls[name] += 1
                if name == "peer-0":
                    if calls[name] == 1:
                        self.forum.set_process_state(agent_id, ProcessState.RUNNING)
                        worker_started.set()
                        await allow_failure.wait()
                        self.forum.set_process_state(agent_id, ProcessState.FAILED, exit_code=1)
                        return 1
                    observed["retry_trigger"] = kwargs["invocation"].env.get("IDEA_TRIGGER_EVENT_ID")
                    self.forum.set_process_state(agent_id, ProcessState.DORMANT, exit_code=0)
                    return 0
                try:
                    await worker_started.wait()
                    self.forum.add_comment(str(thread["id"]), "user", "A new request during execution @peer-0")
                    observed["fresh_event_id"] = str(self.forum.activity_high_water(run_id))
                    allow_failure.set()
                    await self.until(lambda: bool(observed.get("retry_trigger")))
                    await self.until(lambda: not self.forum.pending_notifications(worker_id))
                finally:
                    self.forum.retire_agent(worker_id, "Finished interrupted-delivery test")
                    self.forum.retire_agent(agent_id, "Finished publishing test events")
                return 0

            await asyncio.wait_for(run_reactor(
                forum=self.forum, prepared=prepared, runner=fake_runner,
                poll_interval=0.002, max_concurrent=2,
            ), timeout=6)

        asyncio.run(scenario())
        self.assertEqual(2, calls["peer-0"])
        self.assertEqual(observed.get("fresh_event_id"), observed.get("retry_trigger"))
        self.assertEqual([], self.forum.pending_notifications(worker_id))

    def test_subscription_wake_has_no_explicit_reply_routing(self) -> None:
        prepared = self.prepare(2)
        run_id = str(prepared.run["id"])
        worker_id = str(prepared.peers[0].agent["id"])
        thread = self.forum.create_thread(run_id, "peer-1", "Followed discussion", "Initial note")
        self.forum.subscribe(worker_id, str(thread["id"]), wake=True)
        calls = Counter()
        captured: list[object] = []

        async def fake_runner(**kwargs):
            agent_id = str(kwargs["agent"]["id"])
            name = kwargs["profile"].name
            calls[name] += 1
            if name == "peer-0":
                if calls[name] == 1:
                    self.forum.set_process_state(agent_id, ProcessState.DORMANT, exit_code=0)
                else:
                    captured.append(kwargs["invocation"])
                    self.forum.retire_agent(agent_id, "Reviewed subscription activity")
            else:
                await self.until(lambda: calls["peer-0"] == 1)
                self.forum.add_comment(str(thread["id"]), "peer-1", "New evidence without a mention")
                self.forum.retire_agent(agent_id, "Published update")
            return 0

        asyncio.run(asyncio.wait_for(run_reactor(
            forum=self.forum, prepared=prepared, runner=fake_runner,
            poll_interval=0.002, max_concurrent=2,
        ), timeout=4))
        self.assertEqual(1, len(captured))
        invocation = captured[0]
        self.assertNotIn("IDEA_TRIGGER_EVENT_ID", invocation.env)
        self.assertNotIn("IDEA_TRIGGER_THREAD_ID", invocation.env)
        self.assertNotIn("reply_trigger", invocation.argv[-1])
        self.assertIn("IDEA `reply` tool", invocation.argv[-1])
        self.assertIn('"notification": "subscription"', invocation.argv[-1])

    def test_following_background_excludes_unrelated_public_threads(self) -> None:
        prepared = self.prepare()
        run_id = str(prepared.run["id"])
        agent_id = str(prepared.peers[0].agent["id"])
        followed = self.forum.create_thread(run_id, "someone", "Followed material", "Digest evidence")
        unrelated = self.forum.create_thread(run_id, "someone", "Unrelated material", "Unrelated text")
        self.forum.subscribe(agent_id, str(followed["id"]))
        self.forum.create_thread(run_id, "user", "Direct request", "Please respond @peer-0")
        prompts: list[str] = []

        async def fake_runner(**kwargs):
            prompts.append(kwargs["invocation"].argv[-1])
            self.forum.retire_agent(str(kwargs["agent"]["id"]), "Reviewed direct request")
            return 0

        asyncio.run(asyncio.wait_for(run_reactor(
            forum=self.forum, prepared=prepared, runner=fake_runner, poll_interval=0.002,
        ), timeout=3))
        self.assertEqual(1, len(prompts))
        background = prompts[0].split("BACKGROUND ACTIVITY", 1)[1]
        self.assertIn(str(followed["id"]), background)
        self.assertNotIn(str(unrelated["id"]), prompts[0])
        self.assertNotIn("Unrelated material", prompts[0])
        self.assertNotIn("Unrelated text", prompts[0])

    def test_cancelled_delivery_is_retained_and_resumed_with_persisted_policy(self) -> None:
        prepared = self.prepare()
        run_id = str(prepared.run["id"])
        agent_id = str(prepared.peers[0].agent["id"])
        self.forum.create_thread(run_id, "user", "Unfinished request", "Continue after interruption @peer-0")
        event_id = int(self.forum.pending_notifications(agent_id)[0]["id"])
        observed: list[str] = []

        async def scenario():
            started = asyncio.Event()
            never = asyncio.Event()

            async def interrupted_runner(**kwargs):
                self.forum.set_process_state(agent_id, ProcessState.RUNNING)
                started.set()
                await never.wait()
                return 0

            reactor = asyncio.create_task(run_reactor(
                forum=self.forum, prepared=prepared, runner=interrupted_runner,
                poll_interval=0.002, max_concurrent=1, max_codex=1, max_claude=1,
            ))
            await asyncio.wait_for(started.wait(), timeout=3)
            reactor.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await reactor
            self.assertEqual([event_id], [int(item["id"]) for item in self.forum.pending_notifications(agent_id)])
            resumed = prepare_resume(forum=self.forum, run_id=run_id)

            async def resumed_runner(**kwargs):
                observed.append(kwargs["invocation"].env.get("IDEA_TRIGGER_EVENT_ID", ""))
                self.forum.retire_agent(agent_id, "Completed retained request")
                return 0

            await asyncio.wait_for(run_reactor(
                forum=self.forum, prepared=resumed, runner=resumed_runner, poll_interval=0.002,
            ), timeout=3)

        asyncio.run(scenario())
        self.assertEqual([str(event_id)], observed)
        self.assertEqual([], self.forum.pending_notifications(agent_id))
        policy = ExecutionStore(self.forum, run_id).configure()
        self.assertEqual((1, 1, 1), (policy.max_concurrent, policy.max_codex, policy.max_claude))

    def test_run_lock_rejects_second_owner_until_first_releases(self) -> None:
        prepared = self.prepare()
        run_id = str(prepared.run["id"])
        with RunLock(self.forum.state_dir, run_id) as first:
            self.assertIsNotNone(first.fd)
            os.fstat(first.fd)
            with self.assertRaisesRegex(RuntimeError, "already has a launcher"):
                with RunLock(self.forum.state_dir, run_id):
                    self.fail("Second owner acquired a live run lock")
        with RunLock(self.forum.state_dir, run_id) as successor:
            self.assertIsNotNone(successor.fd)

    def test_fresh_correction_is_delivered_in_first_retry_even_if_retry_fails(self) -> None:
        prepared = self.prepare(2)
        run_id = str(prepared.run["id"])
        worker_id = str(prepared.peers[0].agent["id"])
        thread = self.forum.create_thread(run_id, "user", "Old requests", "Context")
        for index in range(25):
            self.forum.add_comment(str(thread["id"]), "user", f"Old request {index} @peer-0 " + "証拠🙂" * 2_000)
        old_high_water = self.forum.activity_high_water(run_id)
        store = ExecutionStore(self.forum, run_id)
        calls = Counter()
        observed: dict[str, object] = {}
        correction = "NEW CORRECTION: use the updated source document @peer-0"

        async def fake_runner(**kwargs):
            agent_id = str(kwargs["agent"]["id"])
            name = kwargs["profile"].name
            calls[name] += 1
            if name == "peer-0":
                if calls[name] == 2:
                    observed["retry_prompt"] = kwargs["invocation"].argv[-1]
                    observed["retry_trigger"] = kwargs["invocation"].env.get("IDEA_TRIGGER_EVENT_ID")
                self.forum.set_process_state(agent_id, ProcessState.FAILED, exit_code=1)
                return 1
            try:
                await self.until(lambda: store.held_through(worker_id) >= old_high_water)
                # A fresh peer mention must survive even though old human
                # mentions normally precede peers in attention selection.
                self.forum.add_comment(str(thread["id"]), "peer-1", correction)
                observed["fresh_event_id"] = self.forum.activity_high_water(run_id)
                await self.until(lambda: calls["peer-0"] >= 2)
                await self.until(lambda: store.held_through(worker_id) >= int(observed["fresh_event_id"]))
                await asyncio.sleep(0.03)
            finally:
                self.forum.retire_agent(worker_id, "Stop after observing the second failure")
                self.forum.retire_agent(agent_id, "Published the corrective instruction")
            return 0

        asyncio.run(asyncio.wait_for(run_reactor(
            forum=self.forum, prepared=prepared, runner=fake_runner,
            poll_interval=0.002, max_concurrent=2,
        ), timeout=6))
        self.assertEqual(2, calls["peer-0"])
        self.assertIn(correction, str(observed.get("retry_prompt", "")))
        self.assertEqual(str(observed.get("fresh_event_id")), observed.get("retry_trigger"))
        pending = self.forum.pending_notifications(worker_id, limit=100)
        self.assertIn(observed["fresh_event_id"], [int(item["id"]) for item in pending])

    def test_retirement_during_admission_never_starts_or_resurrects_peer(self) -> None:
        for boundary in ("claim", "reset"):
            with self.subTest(boundary=boundary):
                prepared = self.prepare()
                run_id = str(prepared.run["id"])
                agent_id = str(prepared.peers[0].agent["id"])
                self.forum.create_thread(run_id, "user", "Queued request", "Inspect this @peer-0")
                calls: list[str] = []

                async def fake_runner(**kwargs):
                    calls.append(str(kwargs["agent"]["id"]))
                    return 0

                if boundary == "claim":
                    original_claim = ExecutionStore.claim

                    def retire_before_claim(store, *args, **kwargs):
                        self.forum.retire_agent(agent_id, "Retired after the launcher snapshot")
                        return original_claim(store, *args, **kwargs)

                    interception = patch.object(ExecutionStore, "claim", new=retire_before_claim)
                else:
                    original_reset = self.forum.reset_process_observation

                    def retire_before_reset(identifier, **kwargs):
                        self.forum.retire_agent(agent_id, "Retired after claim, before reset")
                        return original_reset(identifier, **kwargs)

                    interception = patch.object(self.forum, "reset_process_observation", side_effect=retire_before_reset)
                with interception:
                    asyncio.run(asyncio.wait_for(run_reactor(
                        forum=self.forum, prepared=prepared, runner=fake_runner, poll_interval=0.002,
                    ), timeout=3))
                self.assertEqual([], calls)
                self.assertEqual("retired", self.forum.get_agent(agent_id)["process_state"])
                self.assertFalse(self.forum.reset_process_observation(agent_id))
                self.forum.set_process_state(agent_id, ProcessState.RUNNING, pid=12345)
                self.assertEqual("retired", self.forum.get_agent(agent_id)["process_state"])
                self.assertEqual(1, len(self.forum.pending_notifications(agent_id)))
                self.assertEqual([], ExecutionStore(self.forum, run_id).queued())

    def test_one_run_lock_can_span_resume_preparation_and_reactor_execution(self) -> None:
        prepared = self.prepare()
        run_id = str(prepared.run["id"])
        agent_id = str(prepared.peers[0].agent["id"])
        self.forum.set_process_state(agent_id, ProcessState.DORMANT, exit_code=0)
        observed_descriptors: list[tuple[int, ...]] = []
        with RunLock(self.forum.state_dir, run_id) as held:
            resumed = prepare_resume(forum=self.forum, run_id=run_id, run_lock=held)
            with self.assertRaises(RuntimeError):
                with RunLock(self.forum.state_dir, run_id):
                    self.fail("Preparation released the externally held lock")

            async def fake_runner(**kwargs):
                observed_descriptors.append(kwargs["invocation"].inherited_fds)
                self.forum.retire_agent(agent_id, "Completed the resumed invocation")
                return 0

            codes = asyncio.run(asyncio.wait_for(run_reactor(
                forum=self.forum, prepared=resumed, runner=fake_runner,
                run_lock=held, poll_interval=0.002,
            ), timeout=3))
            self.assertEqual([0], codes)
            self.assertEqual([(held.fd,)], observed_descriptors)
            os.fstat(held.fd)
            with self.assertRaises(RuntimeError):
                with RunLock(self.forum.state_dir, run_id):
                    self.fail("Reactor released a lock owned by its caller")
        with RunLock(self.forum.state_dir, run_id):
            pass


if __name__ == "__main__":
    unittest.main()
