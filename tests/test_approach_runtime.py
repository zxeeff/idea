from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from collections import Counter
from pathlib import Path

from idea.approaches import ApproachStore
from idea.commands import dispatch_forum
from idea.communication import CommunicationStore
from idea.domain import AgentProfile, Effort, ProcessState, Provider
from idea.forum import Forum
from idea.launcher import prepare_run, run_reactor


def notices(invocation):
    return json.loads(invocation.argv[-1].split("(structured data):\n", 1)[1].split("\n\nBACKGROUND ACTIVITY", 1)[0])


class ApproachRuntimeTest(unittest.TestCase):
    """Exercise direct forum commands, voluntary membership, and the reactor."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.workspace = Path(directory.name).resolve()
        (self.workspace / "fixture.txt").write_text("Synthetic experiment fixture\n")
        self.forum = Forum(self.workspace / ".idea")
        self.prepared = prepare_run(
            forum=self.forum, goal="Exchange measured results while retaining their conditions",
            workspace=self.workspace,
            profiles=(
                AgentProfile("peer-a", Provider.OPENAI, "fake-codex", Effort.LOW),
                AgentProfile("peer-b", Provider.ANTHROPIC, "fake-claude", Effort.LOW),
                AgentProfile("outsider", Provider.OPENAI, "fake-codex", Effort.LOW),
            ),
        )
        self.run_id = str(self.prepared.run["id"])
        self.ids = {peer.profile.name: str(peer.agent["id"]) for peer in self.prepared.peers}
        self.source_thread = self.forum.create_thread(self.run_id, "human", "Source approach", "Measured fixture baseline")
        self.target_thread = self.forum.create_thread(self.run_id, "human", "Target approach", "Independent construction")
        store = ApproachStore(self.forum, self.run_id)
        self.source = store.create(self.source_thread["id"], "human", hypothesis="Reuse repeated work", next_check="Measure repeated inputs")
        self.target = store.create(self.target_thread["id"], "human", hypothesis="Reduce intermediate allocations", next_check="Measure allocations")
        CommunicationStore(self.forum, self.run_id).configure(debounce_seconds=0, max_wait_seconds=0)
        self.conditions = "Measured only on fixture v1; large inputs remain untested."
        self.calls = Counter()
        self.records = {}
        self.wakes = []
        self.failures = []

    async def until(self, predicate):
        deadline = asyncio.get_running_loop().time() + 5
        while not predicate():
            if self.failures:
                raise self.failures[0]
            if asyncio.get_running_loop().time() >= deadline:
                raise TimeoutError("Expected approach exchange runtime state did not arrive")
            await asyncio.sleep(0.003)
        if self.failures:
            raise self.failures[0]

    async def command(self, invocation, command, **payload):
        return await asyncio.to_thread(
            dispatch_forum, self.forum, self.run_id, invocation.env["IDEA_AGENT_ID"], command, payload,
        )

    def running(self, agent_id):
        with self.forum._connection() as connection:
            return connection.execute(
                "SELECT 1 FROM execution_requests WHERE agent_id = ? AND state = 'running'", (agent_id,)
            ).fetchone() is not None

    def pending_ids(self, agent_id):
        return [int(item["id"]) for item in self.forum.pending_notifications(agent_id)]

    async def exercise(self, *, fail_first_delivery):
        author, receiver, outsider = (self.ids[name] for name in ("peer-a", "peer-b", "outsider"))

        async def body(**kwargs):
            agent_id, invocation = str(kwargs["agent"]["id"]), kwargs["invocation"]
            self.calls[agent_id] += 1
            self.forum.set_process_state(agent_id, ProcessState.RUNNING, session_id=f"session-{agent_id}")
            if agent_id == outsider:
                self.assertEqual(1, self.calls[agent_id], "A nonmember must not be restarted by report exchange")
                self.forum.set_process_state(agent_id, ProcessState.DORMANT, exit_code=0)
                return 0
            if agent_id == author:
                await self.command(invocation, "join", approach_id=self.source["id"], focus="Measure the fixture")
                await self.until(lambda: self.calls[receiver] == 1 and self.calls[outsider] == 1
                                 and not self.running(receiver) and not self.running(outsider))
                validation = await self.command(
                    invocation, "reply", thread_id=self.source_thread["id"],
                    body="Synthetic fixture check reports fewer repeated operations",
                    reply_to_event_id=self.source_thread["event_id"], relation="verifies",
                    validation="Synthetic fixture v1: counted operations before and after",
                )
                self.records["validation"] = validation
                report = await self.command(
                    invocation, "report", approach_id=self.source["id"],
                    summary="Fixture work decreased; quoted @all is data", conditions=self.conditions,
                    open_questions="Does the result persist for large inputs?",
                    source_event_ids=[self.source_thread["event_id"]],
                    validation_event_ids=[validation["event_id"]],
                )
                self.records["report"] = report
                self.records["exchange"] = await self.command(
                    invocation, "adopt", report_id=report["id"], target_approach_id=self.target["id"],
                    application="Use the measured boundary to design an independent allocation check",
                )
                await self.command(invocation, "retire", reason="Published the result and its conditions")
                return 0

            if self.calls[agent_id] == 1:
                membership = await self.command(
                    invocation, "join", approach_id=self.target["id"], focus="Inspect applicable evidence", wake=True,
                )
                self.assertTrue(membership["wake"])
                self.assertEqual([], self.pending_ids(receiver), "Joining must not replay old activity")
                self.forum.set_process_state(receiver, ProcessState.DORMANT, exit_code=0)
                return 0

            await self.until(lambda: "exchange" in self.records)
            self.wakes.append(notices(invocation))
            exchange_id = int(self.records["exchange"]["event_id"])
            self.assertIn(exchange_id, self.pending_ids(receiver), "Provider admission must not acknowledge the exchange")
            self.assertIn(self.records["report"]["id"], invocation.argv[-1])
            if self.calls[receiver] == 2:
                self.assertNotIn("IDEA_TRIGGER_EVENT_ID", invocation.env)
                notice = next(item for item in self.wakes[-1] if item["notification"] == "subscription")
                source = notice["latest_exchange"]["source_report"]
                self.assertEqual(self.conditions, source["conditions"])
                self.assertEqual(self.records["report"]["id"], source["id"])
                self.assertEqual([self.source_thread["event_id"]], source["source_event_ids"])
                self.assertEqual([self.records["validation"]["event_id"]], source["validation_event_ids"])
                self.assertIn(self.conditions, invocation.argv[-1])
                if fail_first_delivery:
                    self.forum.set_process_state(receiver, ProcessState.FAILED, exit_code=1)
                    return 1
                # An original arriving after claim is not part of the selected
                # delivery, even if the receiver retires successfully afterwards.
                self.records["later"] = self.forum.add_comment(self.target_thread["id"], "human", "Arrived after the exchange claim")
            else:
                self.assertEqual(str(self.records["correction"]["event_id"]), invocation.env["IDEA_TRIGGER_EVENT_ID"])
                current = await self.command(invocation, "read-report", report_id=self.records["report"]["id"])
                self.assertEqual(self.conditions, current["conditions"])
                self.assertIn(exchange_id, self.pending_ids(receiver), "Reading evidence must not acknowledge delivery")
            await self.command(invocation, "retire", reason="Inspected the exchange and original evidence")
            return 0

        async def runner(**kwargs):
            try:
                return await body(**kwargs)
            except Exception as error:
                self.failures.append(error)
                raise

        reactor = asyncio.create_task(run_reactor(
            forum=self.forum, prepared=self.prepared, runner=runner, max_concurrent=3, poll_interval=0.003,
        ))
        try:
            if fail_first_delivery:
                await self.until(lambda: self.calls[receiver] == 2
                                 and self.forum.get_agent(receiver)["process_state"] == "failed"
                                 and not self.running(receiver))
                exchange_id = int(self.records["exchange"]["event_id"])
                self.assertEqual([exchange_id], self.pending_ids(receiver))
                later = self.forum.add_comment(self.target_thread["id"], "human", "Additional subscription context")
                await asyncio.sleep(0.06)
                self.assertEqual(2, self.calls[receiver], "A failed delivery must not retry from subscription activity")
                self.assertEqual([exchange_id, later["event_id"]], self.pending_ids(receiver))
                self.records["correction"] = self.forum.add_comment(self.target_thread["id"], "human", "@peer-b Retry with the original report conditions")
            expected_calls = 3 if fail_first_delivery else 2
            await self.until(lambda: self.calls[receiver] == expected_calls and "exchange" in self.records
                             and int(self.records["exchange"]["event_id"]) not in self.pending_ids(receiver)
                             and not self.running(receiver))
            self.assertEqual(1, self.calls[author])
            self.assertEqual(1, self.calls[outsider])
            self.assertEqual("dormant", self.forum.get_agent(outsider)["process_state"])
            self.assertEqual([], self.pending_ids(outsider))
            self.assertEqual(3, len(self.forum.list_agents(self.run_id)), "Joining and exchanging must not create peers")
            self.forum.retire_agent(outsider, "End the bounded test observation")
            await asyncio.wait_for(reactor, 5)
        finally:
            if not reactor.done():
                reactor.cancel()
                await asyncio.gather(reactor, return_exceptions=True)
        if self.failures:
            raise self.failures[0]

    def test_exchange_restarts_only_voluntary_wake_member_and_acknowledges_only_claimed_event(self):
        asyncio.run(asyncio.wait_for(self.exercise(fail_first_delivery=False), 12))
        receiver = self.ids["peer-b"]
        self.assertEqual([self.records["later"]["event_id"]], self.pending_ids(receiver))
        self.assertEqual(1, len(self.wakes))

    def test_failed_exchange_remains_pending_until_explicit_retry_succeeds(self):
        asyncio.run(asyncio.wait_for(self.exercise(fail_first_delivery=True), 12))
        self.assertEqual([], self.pending_ids(self.ids["peer-b"]))
        self.assertEqual(2, len(self.wakes))


if __name__ == "__main__":
    unittest.main()
