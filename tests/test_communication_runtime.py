from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from collections import Counter
from pathlib import Path

from idea.communication import CommunicationStore
from idea.domain import AgentProfile, Effort, ProcessState, Provider
from idea.execution import ExecutionStore
from idea.forum import Forum
from idea.launcher import _delivery_ids, prepare_run, run_reactor
from idea.prompts import MAX_WAKE_CONTEXT_BYTES, select_wake_context


def notices(invocation):
    task = invocation.argv[-1]
    return json.loads(task.split("(structured data):\n", 1)[1].split("\n\nBACKGROUND ACTIVITY", 1)[0])


class CommunicationRuntimeTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.workspace = Path(directory.name).resolve()
        self.forum = Forum(self.workspace / ".idea")

    def prepare(self, count=1):
        prepared = prepare_run(
            forum=self.forum, goal="Preserve every original and inspect relevant evidence",
            workspace=self.workspace,
            profiles=tuple(AgentProfile(f"peer-{index}", Provider.OPENAI, "fake-codex", Effort.LOW)
                           for index in range(count)),
        )
        self.run_id = str(prepared.run["id"])
        self.worker_id = str(prepared.peers[0].agent["id"])
        self.thread_id = str(self.forum.create_thread(self.run_id, "writer", "Evidence", "Original claim")["id"])
        self.forum.subscribe(self.worker_id, self.thread_id, wake=True)
        return prepared

    async def until(self, predicate):
        deadline = asyncio.get_running_loop().time() + 3
        while not predicate():
            if asyncio.get_running_loop().time() >= deadline:
                raise TimeoutError("Expected runtime state did not arrive")
            await asyncio.sleep(0.002)

    def pending(self):
        items = []
        cursor = 0
        while page := self.forum.pending_notifications(self.worker_id, limit=100, after=cursor):
            items.extend(page)
            cursor = max(int(item["id"]) for item in page)
        return items

    def test_500_subscription_events_deliver_once_with_all_originals_recoverable(self):
        prepared = self.prepare()
        for index in range(500):
            self.forum.add_comment(self.thread_id, "writer", f"Original evidence {index}: 한글🙂")
        pending = self.pending()
        captured = []

        async def runner(**kwargs):
            invocation = kwargs["invocation"]
            captured.append(notices(invocation))
            # Claims reserve deliveries; they must not acknowledge before completion.
            self.assertEqual(500, len(self.pending()))
            self.assertNotIn("IDEA_TRIGGER_EVENT_ID", invocation.env)
            self.assertLessEqual(len(invocation.argv[-1].split("\n\nOBJECTIVE:", 1)[0].encode()), MAX_WAKE_CONTEXT_BYTES)
            self.forum.retire_agent(self.worker_id, "Received the update range")
            return 0

        asyncio.run(asyncio.wait_for(run_reactor(
            forum=self.forum, prepared=prepared, runner=runner, poll_interval=0.002,
        ), timeout=5))
        self.assertEqual(1, len(captured))
        self.assertEqual(1, len(captured[0]))
        notice = captured[0][0]
        self.assertEqual(500, notice["update_count"])
        self.assertEqual(int(pending[0]["id"]) - 1, notice["after_event"])
        self.assertEqual(int(pending[-1]["id"]), notice["through_event"])
        self.assertEqual("writer", notice["latest_author"])
        self.assertIn("Original evidence 499", notice["latest_message_preview"])
        self.assertNotIn("_delivery_event_ids", notice)
        self.assertEqual([], self.forum.pending_notifications(self.worker_id))
        originals = []
        cursor = notice["after_event"]
        while True:
            page = self.forum.thread_changes(self.thread_id, after_event=cursor, through_event=notice["through_event"])
            originals.extend(page["items"])
            if page["next_cursor"] is None:
                break
            cursor = page["next_cursor"]
        self.assertEqual([item["id"] for item in pending], [item["id"] for item in originals])
        self.assertEqual(500, len(self.forum.get_thread(self.thread_id)["comments"]))

    def test_updates_arriving_during_delivery_wait_for_the_next_invocation(self):
        prepared = self.prepare()
        CommunicationStore(self.forum, self.run_id).configure(debounce_seconds=0, max_wait_seconds=0)
        for index in range(4):
            self.forum.add_comment(self.thread_id, "writer", f"Before claim {index}")
        captured = []

        async def runner(**kwargs):
            captured.append(notices(kwargs["invocation"]))
            if len(captured) == 1:
                self.forum.add_comment(self.thread_id, "writer", "Arrived during provider execution")
                self.forum.set_process_state(self.worker_id, ProcessState.DORMANT, exit_code=0)
            else:
                self.assertEqual(1, len(self.forum.pending_notifications(self.worker_id)))
                self.forum.retire_agent(self.worker_id, "Received both batches")
            return 0

        asyncio.run(asyncio.wait_for(run_reactor(
            forum=self.forum, prepared=prepared, runner=runner, poll_interval=0.002,
        ), timeout=5))
        self.assertEqual([4, 1], [batch[0]["update_count"] for batch in captured])
        self.assertLess(captured[0][0]["through_event"], captured[1][0]["through_event"])
        self.assertEqual([], self.forum.pending_notifications(self.worker_id))

    def test_failed_and_interrupted_group_delivery_preserves_exact_selected_ids(self):
        self.prepare()
        for index in range(3):
            self.forum.add_comment(self.thread_id, "writer", f"First discussion {index}: " + "증거🙂" * 2000)
        other = str(self.forum.create_thread(self.run_id, "writer", "Other evidence", "Other claim")["id"])
        self.forum.subscribe(self.worker_id, other, wake=True)
        for index in range(3):
            self.forum.add_comment(other, "writer", f"Other discussion {index}: " + "증거🙂" * 2000)
        batch = CommunicationStore(self.forum, self.run_id).pending_batch(self.worker_id, immediate=True)
        selected, _, _ = select_wake_context(batch, [], max_bytes=4096)
        self.assertEqual(1, len(selected))
        ids = _delivery_ids(selected)
        all_ids = set(_delivery_ids(batch))
        store = ExecutionStore(self.forum, self.run_id)
        policy = store.configure()
        request = store.enqueue(self.worker_id, kind="start")
        attempt = store.claim(request, ids, policy)
        store.finish(request, attempt, exit_code=1)
        self.assertEqual(all_ids, {int(item["id"]) for item in self.pending()})
        request = store.enqueue(self.worker_id, kind="start")
        interrupted = store.claim(request, ids, policy)
        store.recover()
        store.finish(request, interrupted, exit_code=0)
        self.assertEqual(all_ids, {int(item["id"]) for item in self.pending()})
        resumed = store.claim(request, ids, policy)
        self.assertNotEqual(interrupted, resumed)
        store.finish(request, resumed, exit_code=0)
        self.assertEqual(all_ids - set(ids), {int(item["id"]) for item in self.pending()})

    def test_debounce_keeps_one_queued_request_and_human_mention_bypasses_wait(self):
        prepared = self.prepare(2)
        CommunicationStore(self.forum, self.run_id).configure(debounce_seconds=60, max_wait_seconds=60)
        store = ExecutionStore(self.forum, self.run_id)
        calls = Counter()
        captured = []

        async def runner(**kwargs):
            agent_id = str(kwargs["agent"]["id"])
            calls[agent_id] += 1
            if agent_id == self.worker_id:
                if calls[agent_id] == 1:
                    self.forum.set_process_state(agent_id, ProcessState.DORMANT, exit_code=0)
                else:
                    captured.append(notices(kwargs["invocation"]))
                    self.forum.retire_agent(agent_id, "Handled urgent request with pending updates")
            else:
                await self.until(lambda: calls[self.worker_id] == 1 and not any(
                    request["agent_id"] == self.worker_id for request in store.queued()))
                self.forum.add_comment(self.thread_id, "writer", "First burst update")
                await self.until(lambda: any(request["agent_id"] == self.worker_id for request in store.queued()))
                queued_id = next(request["id"] for request in store.queued() if request["agent_id"] == self.worker_id)
                await asyncio.sleep(0.04)
                self.forum.add_comment(self.thread_id, "writer", "Second burst update")
                await asyncio.sleep(0.04)
                self.assertEqual(1, calls[self.worker_id])
                self.assertEqual(queued_id, next(request["id"] for request in store.queued() if request["agent_id"] == self.worker_id))
                self.forum.add_comment(self.thread_id, "human", "Please inspect this now @peer-0")
                await self.until(lambda: calls[self.worker_id] == 2)
                self.forum.retire_agent(agent_id, "Published updates")
            return 0

        asyncio.run(asyncio.wait_for(run_reactor(
            forum=self.forum, prepared=prepared, runner=runner, poll_interval=0.002,
        ), timeout=5))
        self.assertEqual(1, len(captured))
        self.assertEqual(["mention", "subscription"], [item["notification"] for item in captured[0]])
        self.assertEqual(2, captured[0][1]["update_count"])
        self.assertEqual([], self.forum.pending_notifications(self.worker_id))


if __name__ == "__main__":
    unittest.main()
