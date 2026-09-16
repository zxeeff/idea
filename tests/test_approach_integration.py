from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from idea.approaches import ApproachStore
from idea.commands import dispatch_forum
from idea.communication import CommunicationStore
from idea.domain import AgentProfile, Effort, ProcessState, Provider
from idea.execution import ExecutionStore
from idea.forum import Forum
from idea.population import PopulationPolicy, PopulationStore
from idea.reports import ReportStore


class ApproachIntegrationTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.forum = Forum(self.root / "state")
        self.run_id = self.forum.create_run("Explore distinct approaches", self.root)["id"]
        self.agents = [self.forum.register_agent(
            self.run_id, AgentProfile(f"peer-{index}", Provider.OPENAI, "test", Effort.LOW)
        ) for index in range(3)]
        self.approaches = ApproachStore(self.forum, self.run_id)
        self.reports = ReportStore(self.forum, self.run_id)
        self.source_thread = self.forum.create_thread(self.run_id, "human", "Source", "Original observation")
        self.target_thread = self.forum.create_thread(self.run_id, "human", "Target", "Independent investigation")
        self.source = self.approaches.create(self.source_thread["id"], "human", hypothesis="Approach A",
                                            next_check="Check the boundary case")
        self.target = self.approaches.create(self.target_thread["id"], "human", hypothesis="Approach B",
                                            next_check="Try a different construction")

    def dispatch(self, index, command, **payload):
        return dispatch_forum(self.forum, self.run_id, self.agents[index]["id"], command, payload)

    def test_join_digest_and_explicit_wake_have_separate_delivery_semantics(self):
        worker = self.agents[0]["id"]
        before = self.forum.activity_high_water(self.run_id)
        self.dispatch(0, "join", approach_id=self.target["id"], focus="Investigate the boundary")
        self.assertEqual(before, self.forum.activity_high_water(self.run_id))
        self.assertEqual([], self.forum.pending_notifications(worker))
        comment = self.forum.add_comment(self.target_thread["id"], "human", "New evidence")
        self.assertEqual([], self.forum.pending_notifications(worker))
        digest = self.forum.activity_page(worker, scope="following", after=before)
        self.assertEqual([comment["event_id"]], [item["id"] for item in digest["items"]])
        self.dispatch(0, "join", approach_id=self.target["id"], wake=True)
        self.assertEqual([], self.forum.pending_notifications(worker))
        next_comment = self.forum.add_comment(self.target_thread["id"], "human", "Wake chosen by the receiver")
        self.assertEqual([next_comment["event_id"]], [item["id"] for item in self.forum.pending_notifications(worker)])
        self.assertEqual(3, len(self.forum.list_agents(self.run_id)))
        self.assertEqual("created", self.forum.get_agent(worker)["process_state"])

    def test_join_leave_and_explicit_follow_do_not_overwrite_each_other(self):
        worker = self.agents[0]["id"]
        target = self.target_thread["id"]
        self.forum.subscribe(worker, target, wake=True)
        self.approaches.join(self.target["id"], worker, wake=True)
        comment = self.forum.add_comment(target, "human", "One notification from two chosen interests")
        self.assertEqual([comment["event_id"]], [item["id"] for item in self.forum.pending_notifications(worker)])
        self.forum.acknowledge_notifications(worker, [comment["event_id"]])
        self.approaches.leave(self.target["id"], worker)
        comment = self.forum.add_comment(target, "human", "Explicit subscription survives leave")
        self.assertEqual([comment["event_id"]], [item["id"] for item in self.forum.pending_notifications(worker)])
        self.forum.acknowledge_notifications(worker, [comment["event_id"]])
        self.approaches.join(self.target["id"], worker, wake=True)
        self.forum.unsubscribe(worker, target)
        comment = self.forum.add_comment(target, "human", "Membership interest survives unfollow")
        self.assertEqual([comment["event_id"]], [item["id"] for item in self.forum.pending_notifications(worker)])
        self.approaches.leave(self.target["id"], worker)
        self.forum.add_comment(target, "human", "No longer interested")
        self.assertEqual([comment["event_id"]], [item["id"] for item in self.forum.pending_notifications(worker)])
        self.assertEqual([], self.forum.list_subscriptions(worker)["items"])

    def test_report_exchange_notifies_only_chosen_target_members_and_preserves_objections(self):
        worker = self.agents[0]["id"]
        outsider = self.agents[1]["id"]
        self.approaches.join(self.target["id"], worker, wake=True)
        report = self.dispatch(2, "report", approach_id=self.source["id"], summary="A result containing quoted @all",
                               conditions="Only tested on the small fixture", open_questions="Large inputs remain unresolved",
                               source_event_ids=[self.source_thread["event_id"]])
        exchange = self.dispatch(2, "adopt", report_id=report["id"], target_approach_id=self.target["id"],
                                 application="Use the boundary observation to construct an independent check")
        self.assertEqual([exchange["event_id"]], [item["id"] for item in self.forum.pending_notifications(worker)])
        self.assertEqual([], self.forum.pending_notifications(outsider))
        self.assertEqual([], self.forum.pending_notifications(self.agents[2]["id"]))
        self.forum.add_comment(self.source_thread["id"], "human", "Later evidence challenges the premise",
                               reply_to_event_id=self.source_thread["event_id"], relation="challenges")
        refreshed = self.dispatch(0, "read-report", report_id=report["id"])
        self.assertEqual("Only tested on the small fixture", refreshed["conditions"])
        self.assertEqual(1, refreshed["source_changes"]["total_count"])
        page = self.forum.thread_changes(self.target_thread["id"], after_event=exchange["event_id"] - 1)
        received = next(item for item in page["items"] if item["id"] == exchange["event_id"])
        self.assertEqual(report["id"], received["exchange"]["report_id"])
        self.assertEqual(1, received["exchange"]["source_report"]["source_changes"]["total_count"])
        self.assertEqual(self.target["id"], received["approach"]["id"])

    def test_recruitment_prefers_existing_voluntary_members_without_assigning_work(self):
        population = PopulationStore(self.forum, self.run_id)
        population.configure(policy=PopulationPolicy(initial_agents=3, max_agents=10))
        for agent in self.agents:
            self.forum.set_process_state(agent["id"], ProcessState.DORMANT, session_id=f"session-{agent['id']}")
        # The earlier nonmember is otherwise eligible and wins the old FIFO rule.
        self.approaches.join(self.target["id"], self.agents[2]["id"])
        call = population.open_call(self.agents[0]["id"], self.target_thread["id"], "An independent check would help")
        store = ExecutionStore(self.forum, self.run_id)
        policy = store.configure(max_concurrent=3)
        birth = population.reserve_birth(execution_policy=policy, available_agent_ids=[a["id"] for a in self.agents])
        self.assertIsNone(birth)
        with self.forum._connection() as connection:
            offered = connection.execute("SELECT agent_id FROM participation_offers WHERE call_id=?", (call["id"],)).fetchone()[0]
        self.assertEqual(self.agents[2]["id"], offered)
        self.assertEqual("open", population.get_call(call["id"])["state"])
        self.assertEqual(3, len(self.forum.list_agents(self.run_id)))

    def test_group_retains_report_references_when_latest_update_is_ordinary_comment(self):
        worker = self.agents[0]["id"]
        self.approaches.join(self.target["id"], worker, wake=True)
        reports = [self.reports.publish(
            self.target["id"], "human", summary=f"Result {number}", conditions="Small fixture only",
            source_event_ids=[self.target_thread["event_id"]],
        ) for number in range(12)]
        last = self.forum.add_comment(self.target_thread["id"], "human", "An ordinary follow-up")
        batch = CommunicationStore(self.forum, self.run_id).pending_batch(worker, immediate=True)
        self.assertEqual(1, len(batch))
        group = batch[0]
        self.assertEqual(last["event_id"], group["through_event_id"])
        self.assertEqual(13, group["event_count"])
        self.assertEqual(12, group["report_event_count"])
        self.assertEqual([r["id"] for r in reports[:4] + reports[-4:]],
                         [r["report_id"] for r in group["report_events"]])
        self.assertEqual(self.target["id"], group["approach"]["id"])
        self.assertNotIn("report", group)
        self.assertEqual(13, len(self.forum.pending_notifications(worker)))
        first = self.forum.get_comment(self.target_thread["id"], reports[0]["comment_id"])
        self.assertEqual(reports[0]["id"], first["report"]["id"])

    def test_report_references_cover_only_the_selected_notification_prefix(self):
        worker = self.agents[0]["id"]
        self.approaches.join(self.target["id"], worker, wake=True)
        reports = [self.reports.publish(
            self.target["id"], "human", summary=f"Result {number}", conditions="Small fixture only",
            source_event_ids=[self.target_thread["event_id"]],
        ) for number in range(3)]
        store = CommunicationStore(self.forum, self.run_id)
        with mock.patch.object(CommunicationStore, "SUBSCRIPTION_LIMIT", 2):
            group = store.pending_batch(worker, immediate=True)[0]
        self.assertEqual([r["event_id"] for r in reports[:2]], group["_delivery_event_ids"])
        self.assertEqual([r["id"] for r in reports[:2]], [r["report_id"] for r in group["report_events"]])
        self.forum.acknowledge_notifications(worker, group["_delivery_event_ids"])
        remaining = store.pending_batch(worker, immediate=True)[0]
        self.assertEqual(1, remaining["report_event_count"])
        self.assertEqual(reports[2]["id"], remaining["report"]["id"])

    def test_existing_forum_upgrade_preserves_original_posts_and_pending_deliveries(self):
        legacy = Forum(self.root / "legacy")
        run = legacy.create_run("Existing objective", self.root)
        peer = legacy.register_agent(run["id"], AgentProfile("existing-peer", Provider.OPENAI, "test", Effort.LOW))
        thread = legacy.create_thread(run["id"], "human", "Existing discussion", "@existing-peer preserve this request")
        before = legacy.activity_high_water(run["id"])
        with legacy._connection() as connection:
            # These are precisely the new tables; the established forum records
            # and notification ledger predate this feature and remain in place.
            for table in ("report_exchanges", "report_event_references", "approach_reports", "approach_members", "approaches"):
                connection.execute(f"DROP TABLE {table}")
        upgraded = Forum(legacy.state_dir)
        Forum(legacy.state_dir)  # Subsequent clients must be harmless too.
        self.assertEqual(before, upgraded.activity_high_water(run["id"]))
        self.assertEqual("Existing objective", upgraded.get_run(run["id"])["goal"])
        self.assertEqual(thread["body"], upgraded.get_thread(thread["id"])["body"])
        self.assertEqual([thread["event_id"]], [e["id"] for e in upgraded.pending_notifications(peer["id"])])
        approach = ApproachStore(upgraded, run["id"]).create(
            thread["id"], "human", hypothesis="Extend the existing discussion", next_check="Preserve old evidence",
        )
        report = ReportStore(upgraded, run["id"]).publish(
            approach["id"], "human", summary="Old evidence is still available", conditions="Existing thread",
            source_event_ids=[thread["event_id"]],
        )
        self.assertEqual([thread["event_id"]], report["source_event_ids"])


if __name__ == "__main__":
    unittest.main()
