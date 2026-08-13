from __future__ import annotations

import unittest

from idea.domain import AgentProfile, Effort, Provider
from idea.profiles import default_profiles
from idea.prompts import shared_prompt, user_task


class ProfilesAndPromptsTest(unittest.TestCase):
    def test_defaults_vary_models_and_effort_without_roles_or_phases(self) -> None:
        profiles = default_profiles()
        self.assertEqual(16, len(profiles))
        self.assertEqual(
            {"gpt-5.6-luna", "gpt-5.6-terra", "gpt-5.6-sol", "sonnet", "opus"},
            {profile.model for profile in profiles},
        )
        self.assertEqual(4, sum(profile.model == "gpt-5.6-sol" for profile in profiles))
        self.assertEqual(
            2,
            sum(
                profile.model == "gpt-5.6-sol" and profile.effort is Effort.MAX
                for profile in profiles
            ),
        )
        self.assertEqual(6, sum(profile.model == "opus" for profile in profiles))
        for effort in (Effort.HIGH, Effort.XHIGH, Effort.MAX):
            self.assertEqual(
                2,
                sum(
                    profile.model == "opus" and profile.effort is effort
                    for profile in profiles
                ),
            )
        self.assertTrue({Effort.LOW, Effort.MEDIUM, Effort.HIGH, Effort.XHIGH, Effort.MAX} <= {
            profile.effort for profile in profiles
        })
        for profile in profiles:
            self.assertFalse(hasattr(profile, "role"))
            self.assertFalse(hasattr(profile, "phase"))
            self.assertFalse(hasattr(profile, "mission"))

    def test_top_level_prompt_is_lean_and_exposes_only_peer_names(self) -> None:
        profile = AgentProfile("peer-one", Provider.OPENAI, "hidden-self-model", Effort.LOW)
        peer = AgentProfile("peer-two", Provider.ANTHROPIC, "hidden-peer-model", Effort.MAX)
        prompt = shared_prompt(
            name=profile.name,
            peer_names=(profile.name, peer.name),
        )
        self.assertLess(len(prompt), 1_000)
        self.assertIn('"peer-one"', prompt)
        self.assertIn('"peer-two"', prompt)
        self.assertIn("forum --help", prompt)
        self.assertIn("reply-trigger", prompt)
        self.assertIn("@all", prompt)
        self.assertIn("@human", prompt)
        self.assertIn("forum retire", prompt)
        self.assertNotIn(profile.model, prompt)
        self.assertNotIn(peer.model, prompt)
        self.assertNotIn(profile.provider.value, prompt)
        self.assertNotIn(peer.provider.value, prompt)
        self.assertNotIn("effort", prompt.casefold())

        defaults = default_profiles()
        default_prompt = shared_prompt(
            name=defaults[0].name,
            peer_names=(peer.name for peer in defaults),
        )
        self.assertLess(len(default_prompt), 1_000)

    def test_user_goal_is_sent_once_at_user_level(self) -> None:
        goal = "--inspect the parser for boundary bugs"
        task = user_task(goal)
        self.assertEqual(f"OBJECTIVE:\n{goal}", task)
        self.assertEqual(1, task.count(goal))


if __name__ == "__main__":
    unittest.main()
