from __future__ import annotations

import json
import unittest

from idea.launcher import _delivery_ids
from idea.prompts import MAX_WAKE_CONTEXT_BYTES, _render_context, select_wake_context, wake_task


def group(index, count=500):
    first = (index - 1) * count + 1
    last = index * count
    return dict(id=last, thread_id=f"thread-{index}", author="forum", latest_author="writer",
                kind="thread_updates", notification_reason="subscription", notification_mode="passive",
                first_event_id=first, through_event_id=last, event_count=count,
                content="한글🙂" * 2000, _delivery_event_ids=list(range(first, last + 1)))


class CommunicationPromptsTest(unittest.TestCase):
    def test_large_batches_fit_without_exposing_internal_delivery_lists(self):
        groups = [group(index) for index in range(1, 21)]
        selected, background, overflow = select_wake_context(groups, [])
        self.assertGreater(len(selected), 0)
        self.assertLess(len(selected), len(groups))
        rendered = _render_context(selected, background, overflow)
        self.assertLessEqual(len(rendered.encode()), MAX_WAKE_CONTEXT_BYTES - 512)
        self.assertNotIn("_delivery_event_ids", rendered)
        self.assertIn("not a summary", rendered)
        self.assertEqual(list(range(1, len(selected) * 500 + 1)), _delivery_ids(selected))
        self.assertTrue(set(_delivery_ids(selected)).isdisjoint(_delivery_ids(groups[len(selected):])))

    def test_small_budget_retains_original_read_range_and_exact_goal(self):
        item = group(1)
        selected, background, overflow = select_wake_context([item], [], max_bytes=2048)
        task = wake_task("목표🙂", selected, background, overflow=overflow)
        rendered = task.split("\n\nOBJECTIVE:\n")[0]
        self.assertLessEqual(len(rendered.encode()), 2048 - 512)
        values = json.loads(rendered.split("(structured data):\n", 1)[1].split("\n\nBACKGROUND", 1)[0])
        self.assertEqual(500, values[0]["update_count"])
        self.assertEqual(0, values[0]["after_event"])
        self.assertEqual(500, values[0]["through_event"])
        self.assertIn("--through-event 500", values[0]["read_command"])
        self.assertEqual(1, task.count("목표🙂"))

    def test_unbounded_validation_is_clipped_and_source_links_survive(self):
        item = group(1)
        item["provenance"] = dict(reply_to_event_id=77, relation="verifies", artifact_id="artifact-v1",
                                  validation="\x00한글" * 10000, evidence_event_ids=[80, 81])
        selected, background, overflow = select_wake_context([item], [])
        task = _render_context(selected, background, overflow)
        self.assertIn('"reply_to_event_id": 77', task)
        self.assertIn('"relation": "verifies"', task)
        self.assertIn('"artifact_id": "artifact-v1"', task)
        self.assertIn('"validation_truncated": true', task)
        self.assertLess(len(task.encode()), 6000)

    def test_minimum_budget_with_many_large_event_references(self):
        groups = [group(index) for index in range(1, 21)]
        for item in groups:
            item.update(id=9000000000000000000, thread_id="thread_" + "a" * 57,
                        first_event_id=8999999999999999000, through_event_id=9000000000000000000)
        background = [dict(id=8000000000000000000 + index, thread_id="thread_" + "b" * 57)
                      for index in range(30)]
        selected, context, overflow = select_wake_context(groups, background, max_bytes=2048)
        self.assertIs(groups[0], selected[0])
        self.assertEqual(19, overflow["omitted_trigger_count"])
        self.assertLessEqual(len(_render_context(selected, context, overflow).encode()), 2048 - 512)

    def test_upstream_validation_truncation_survives_an_already_short_preview(self):
        item = group(1)
        item["provenance"] = dict(relation="verifies", validation="a" * 512, validation_truncated=True)
        selected, background, overflow = select_wake_context([item], [])
        task = _render_context(selected, background, overflow)
        self.assertIn('"latest_provenance"', task)
        self.assertIn('"validation_truncated": true', task)


if __name__ == "__main__":
    unittest.main()
