from __future__ import annotations

import json
import unittest

from idea.prompts import MAX_WAKE_CONTEXT_BYTES, _preview, _render_context, select_wake_context, shared_prompt, wake_task


class ReportPromptsTest(unittest.TestCase):
    def event(self, number):
        report = dict(id=f"report_{number}", approach_id="approach_source", summary="결론🙂" * 2000,
                      conditions="Only checked against the small fixture", open_questions="Large inputs remain untested",
                      source_event_ids=[1], validation_event_ids=[2],
                      source_changes={"total_count": 1, "items": [dict(event_id=3, thread_id="thread_source", relation="challenges")]},
                      superseded_by={"total_count": 0, "items": []})
        return dict(id=number, thread_id="thread_target", kind="comment", author="peer",
                    notification_reason="subscription", content="Intermediate report shared here",
                    approach=dict(id="approach_target", hypothesis="Independent construction", next_check="Reproduce the boundary"),
                    exchange=dict(report_id=report["id"], application="Use the observation in an independent test", source_report=report))

    def test_conditions_and_objections_travel_together_in_bounded_wake_context(self):
        events = [self.event(number) for number in range(10, 110)]
        selected, background, overflow = select_wake_context(events, [])
        task = wake_task("원래 목표", selected, background, overflow=overflow)
        self.assertLess(len(selected), len(events))
        self.assertIn("Only checked against the small fixture", task)
        self.assertIn("Large inputs remain untested", task)
        self.assertIn('"relation": "challenges"', task)
        self.assertIn("idea forum read-report REPORT_ID --json", task)
        self.assertLessEqual(len(task.split("\n\nOBJECTIVE:")[0].encode()), MAX_WAKE_CONTEXT_BYTES)
        self.assertEqual(1, task.count("원래 목표"))

    def test_small_budget_keeps_source_thread_access_without_unbounded_metadata(self):
        selected, background, overflow = select_wake_context([self.event(n) for n in range(10, 110)], [], max_bytes=2048)
        context = _render_context(selected, background, overflow)
        self.assertTrue(selected)
        self.assertLessEqual(len(context.encode()), 2048 - 512)
        self.assertIn("thread_target", context)
        self.assertIn("idea forum read THREAD_ID --json", context)

    def test_report_wake_does_not_enlarge_the_permanent_prompt(self):
        before = shared_prompt(name="current-peer", peer_names=())
        wake_task("Goal", [self.event(10)])
        after = shared_prompt(name="current-peer", peer_names=())
        self.assertEqual(before, after)
        self.assertLess(len(after.encode()), 800)

    def test_new_report_version_keeps_report_and_event_identifiers_distinct(self):
        event = self.event(10)
        report = event["exchange"]["source_report"]
        report["superseded_by"] = {"total_count": 1, "items": [
            {"id": "report_new_version", "event_id": 123, "thread_id": "thread_source"},
        ]}
        report["conditions_truncated"] = True
        event["exchange"]["content_truncated"] = True
        preview = _preview(event, 2000)["exchange"]
        version = preview["source_report"]["superseded_by"]["items"][0]
        self.assertEqual(123, version["event_id"])
        self.assertEqual("report_new_version", version["report_id"])
        self.assertTrue(preview["source_report"]["conditions_truncated"])
        self.assertTrue(preview["application_truncated"])


if __name__ == "__main__":
    unittest.main()
