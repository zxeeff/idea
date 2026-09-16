from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from idea.domain import AgentProfile, Effort, ProcessState, Provider
from idea.forum import Forum
from idea.execution import ExecutionStore
from idea.population import PopulationPolicy, PopulationStore


class ParticipationNotificationsTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.forum = Forum(Path(self.directory.name) / "state")
        self.run_id = str(self.forum.create_run("Improve a document", self.directory.name)["id"])
        self.peers = [self.forum.register_agent(
            self.run_id, AgentProfile(name, Provider.OPENAI, "fake-model", Effort.LOW),
        ) for name in ("resident-peer", "parked-peer")]

    def test_parking_preserves_identity_but_excludes_broadcasts(self):
        resident, parked = self.peers
        self.forum.set_process_state(parked["id"], ProcessState.DORMANT, session_id="existing-session")
        with self.forum._connection() as connection:
            connection.execute("UPDATE agents SET participation_state='parked',parked_at=? WHERE id=?",
                               ("2026-01-01T00:00:00+00:00", parked["id"]))
        self.forum.create_thread(self.run_id, "human", "General update", "New evidence @all")
        self.assertEqual(1, len(self.forum.pending_notifications(resident["id"])))
        self.assertEqual([], self.forum.pending_notifications(parked["id"]))
        self.forum.create_thread(self.run_id, "human", "Specific follow-up", "@parked-peer Please inspect this")
        self.assertEqual("mention", self.forum.pending_notifications(parked["id"])[0]["notification_reason"])
        reopened = Forum(self.forum.state_dir)
        record = reopened.get_agent(parked["id"])
        self.assertEqual("existing-session", record["session_id"])
        self.assertEqual("dormant", record["process_state"])
        self.assertEqual("parked", record["participation_state"])
        found = reopened.search_agents(self.run_id, query="parked-peer")["items"][0]
        self.assertEqual("parked", found["participation_state"])

    def test_withdrawn_delivery_remains_in_history_without_being_acknowledged(self):
        peer = self.peers[0]
        thread = self.forum.create_thread(self.run_id, "human", "Withdrawn invitation", "Public evidence")
        event_id = self.forum.activity_high_water(self.run_id)
        with self.forum._connection() as connection:
            connection.execute("""INSERT INTO notification_deliveries
                (agent_id,event_id,run_id,notification_reason,priority,withdrawn_at)
                VALUES (?,?,?,'invitation',3,?)""",
                (peer["id"], event_id, self.run_id, "2026-01-01T00:00:00+00:00"))
        self.assertEqual([], self.forum.pending_notifications(peer["id"]))
        self.assertEqual([], self.forum.pending_notification_agents(self.run_id))
        self.assertEqual({"pending_events": 0, "pending_agents": 0}, self.forum.notification_counts(self.run_id))
        with self.forum._connection() as connection:
            row = connection.execute("SELECT * FROM notification_deliveries WHERE event_id=?", (event_id,)).fetchone()
        self.assertIsNone(row["acknowledged_at"])
        self.assertEqual("Public evidence", self.forum.get_thread(thread["id"])["body"])
        self.forum.create_thread(self.run_id, "human", "Actual request", "@resident-peer Please continue")
        self.assertEqual(1, self.forum.notification_counts(self.run_id)["pending_events"])
        self.assertEqual("mention", self.forum.pending_notifications(peer["id"])[0]["notification_reason"])

    def test_withdrawal_between_preview_and_claim_does_not_spend_budget(self):
        peer = self.peers[0]
        population = PopulationStore(self.forum, self.run_id)
        population.configure(
            [AgentProfile("template", Provider.OPENAI, "fake-model", Effort.LOW)],
            PopulationPolicy(initial_agents=2, max_agents=2),
        )
        execution = ExecutionStore(self.forum, self.run_id, population=population)
        policy = execution.configure(max_concurrent=1)
        self.forum.create_thread(self.run_id, "system", "An invitation", "Optional participation")
        invitation_id = self.forum.activity_high_water(self.run_id)
        with self.forum._connection() as connection:
            connection.execute("""INSERT INTO notification_deliveries
                (agent_id,event_id,run_id,notification_reason,priority)
                VALUES (?,?,?,'invitation',3)""", (peer["id"], invitation_id, self.run_id))
        self.forum.create_thread(self.run_id, "human", "Please continue", "@resident-peer Specific evidence")
        human_id = self.forum.activity_high_water(self.run_id)
        event_ids = [event["id"] for event in self.forum.pending_notifications(peer["id"])]
        request_id = execution.enqueue(peer["id"])
        with self.forum._connection() as connection:
            connection.execute("UPDATE notification_deliveries SET withdrawn_at=? WHERE event_id=?",
                               ("2026-01-01T00:00:00+00:00", invitation_id))
        self.assertIsNone(execution.claim(request_id, event_ids, policy))
        self.assertEqual(0, population.summary()["invocations_started"])
        self.assertEqual([human_id], [event["id"] for event in self.forum.pending_notifications(peer["id"])])
        self.assertIsNotNone(execution.claim(request_id, [human_id], policy))
        self.assertEqual(1, population.summary()["invocations_started"])
