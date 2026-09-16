from __future__ import annotations

import json
import unittest

from idea.prompts import (
    MAX_WAKE_CONTEXT_BYTES,
    _render_context,
    blocked_restart_task,
    resume_task,
    select_wake_context,
    shared_prompt,
    wake_task,
)


def event(identifier: int, *, reason: str = "mention", author: str = "peer", content: str = "New evidence") -> dict[str, object]:
    return {
        "id": identifier,
        "notification_reason": reason,
        "notification_mode": "passive" if reason == "subscription" else "targeted",
        "author": author,
        "kind": "comment",
        "subject_id": f"comment-{identifier}",
        "thread_id": f"thread-{identifier}",
        "thread_title": "Evidence and next steps",
        "content": content,
    }


class AttentionPromptsTest(unittest.TestCase):
    def test_peer_directory_replaces_roster_without_consuming_it(self) -> None:
        def unavailable_roster():
            raise AssertionError("The shared prompt must not enumerate peers")
            yield "unused"

        prompt = shared_prompt(name="current-peer", peer_names=unavailable_roster())
        self.assertIn('"current-peer"', prompt)
        self.assertLess(len(prompt.encode("utf-8")), 800)
        self.assertIn('"$IDEA_PYTHON" -m idea forum --help', prompt)
        for capability in ("share findings", "discover peers", "follow discussions", "before retiring"):
            self.assertIn(capability, prompt)
        self.assertNotIn("--after", prompt)
        self.assertNotIn("--limit", prompt)
        long_names = ["other-peer-" + "한" * 10_000 for _ in range(500)]
        self.assertEqual(prompt, shared_prompt(name="current-peer", peer_names=long_names))

    def test_many_unicode_events_are_bounded_with_recoverable_overflow(self) -> None:
        triggers = [event(index, content="한글🙂" * 2_000) for index in range(1, 501)]
        background = [event(index, reason="subscription", author="이름" * 3_000) for index in range(501, 1001)]
        selected, selected_background, overflow = select_wake_context(triggers, background)
        self.assertGreater(len(selected), 0)
        self.assertLess(len(selected), len(triggers))
        self.assertIs(selected[0], triggers[0])
        self.assertEqual(500 - len(selected), overflow["omitted_trigger_count"])
        self.assertEqual(500 - len(selected_background), overflow["omitted_background_count"])
        rendered = _render_context(selected, selected_background, overflow)
        self.assertLessEqual(len(rendered.encode("utf-8")), MAX_WAKE_CONTEXT_BYTES - 512)
        self.assertIn('"message_truncated": true', rendered)
        self.assertIn("omitted events remain pending", rendered)
        self.assertIn(f'"event_id": {len(selected) + 1}', rendered)
        self.assertIn(f'"thread_id": "thread-{len(selected) + 1}"', rendered)
        self.assertIn("idea forum read THREAD_ID --json", rendered)
        self.assertIn("--after CURSOR", rendered)
        self.assertEqual("한글🙂" * 2_000, selected[0]["content"])

    def test_escaped_control_characters_and_long_metadata_share_total_budget(self) -> None:
        triggers = [event(index, content="\x00\n\t\"\\" * 20_000) for index in range(1, 51)]
        for item in triggers:
            item.update(author="作者" * 10_000, thread_title="\x00" * 10_000, attachment_name="🙂" * 10_000)
        selected, background, overflow = select_wake_context(triggers, [], max_bytes=4_096)
        self.assertTrue(selected)
        rendered = _render_context(selected, background, overflow)
        self.assertLessEqual(len(rendered.encode("utf-8")), 4_096 - 512)
        trigger_json = rendered.split("(structured data):\n", 1)[1].split("\n\nBACKGROUND ACTIVITY", 1)[0]
        values = json.loads(trigger_json)
        self.assertEqual(1, values[0]["event_id"])
        self.assertTrue(values[0]["message_truncated"])

    def test_small_supported_budget_retains_first_trigger(self) -> None:
        triggers = [event(index, content="🙂" * 20_000) for index in range(1, 50)]
        selected, background, overflow = select_wake_context(triggers, [], max_bytes=2_048)
        self.assertTrue(selected)
        self.assertEqual(1, selected[0]["id"])
        self.assertLessEqual(len(_render_context(selected, background, overflow).encode("utf-8")), 2_048 - 512)
        with self.assertRaises(ValueError):
            select_wake_context(triggers, [], max_bytes=512)

    def test_user_goal_remains_exact_once_for_wake_and_restart(self) -> None:
        goal = "원래 목표🙂\n" * 20_000
        triggers = [event(index, content="증거🙂" * 10_000) for index in range(100)]
        for function in (wake_task, blocked_restart_task):
            with self.subTest(function=function.__name__):
                task = function(goal, iter(triggers))
                prefix, objective = task.split("\n\nOBJECTIVE:\n", 1)
                self.assertEqual(goal, objective)
                self.assertEqual(1, task.count(goal))
                self.assertLessEqual(len(prefix.encode("utf-8")), MAX_WAKE_CONTEXT_BYTES)
        self.assertTrue(resume_task(goal).endswith(goal))

    def test_subscription_activation_uses_thread_reply(self) -> None:
        subscription = event(3, reason="subscription")
        for function in (wake_task, blocked_restart_task):
            with self.subTest(function=function.__name__):
                task = function("Continue the objective", [subscription])
                self.assertIn("IDEA `reply` tool", task)
                self.assertNotIn("reply_trigger", task)
                self.assertIn('"notification": "subscription"', task)

    def test_human_mentions_precede_peer_mentions_and_subscriptions(self) -> None:
        triggers = [
            event(1, reason="subscription", author="user"),
            event(2),
            event(3, reason="broadcast", author="user"),
            event(4, author="human"),
        ]
        selected, background, overflow = select_wake_context(triggers, [])
        self.assertEqual([4, 2, 3, 1], [item["id"] for item in selected])
        task = wake_task("Continue", selected, background, overflow=overflow)
        self.assertIn("IDEA `reply_trigger` tool for event 4", task)
        self.assertIn("IDEA `reply` tool", task)
        self.assertNotIn("event 1", task)

    def test_legacy_mentions_work_but_passive_activity_is_not_a_mention(self) -> None:
        mention = event(1)
        del mention["notification_reason"]
        task = wake_task("Continue", [mention])
        self.assertIn("IDEA `reply_trigger` tool for event 1", task)
        mention["notification_mode"] = "passive"
        self.assertNotIn("reply_trigger", wake_task("Continue", [mention]))

    def test_preselected_overflow_survives_prompt_construction(self) -> None:
        triggers = [event(index, content="new evidence " * 2_000) for index in range(50)]
        selected, background, overflow = select_wake_context(triggers, [], max_bytes=4_096)
        task = wake_task("Continue", selected, background, overflow=overflow)
        self.assertIn(f'"omitted_trigger_count": {50 - len(selected)}', task)
        self.assertLessEqual(len(task.encode("utf-8")), 4_096)

    def test_new_corrective_trigger_survives_large_older_human_backlog(self) -> None:
        older = [event(index, author="human", content="Old evidence🙂" * 2_000) for index in range(1, 26)]
        fresh = event(26, author="peer", content="Corrective instruction which unlocked this retry")
        fresh["activation_trigger"] = True
        selected, background, overflow = select_wake_context([*older, fresh], [], max_bytes=4_096)
        self.assertIs(fresh, selected[0])
        self.assertLess(len(selected), len(older) + 1)
        task = wake_task("Continue", selected, background, overflow=overflow)
        self.assertIn("Corrective instruction which unlocked this retry", task)
        self.assertIn("IDEA `reply_trigger` tool for event 26", task)
        self.assertLessEqual(len(task.encode("utf-8")), 4_096)


if __name__ == "__main__":
    unittest.main()
