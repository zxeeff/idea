from __future__ import annotations

import unittest
from pathlib import Path

from idea.domain import AgentProfile, Effort, Provider
from idea.profiles import default_profiles
from idea.prompts import shared_prompt


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

    def test_top_level_prompt_exposes_forum_but_leaves_strategy_free(self) -> None:
        profile = AgentProfile("peer", Provider.OPENAI, "gpt-5.6-luna", Effort.LOW)
        prompt = shared_prompt(
            goal="obtain the flag",
            workspace=Path("/tmp/challenge"),
            profile=profile,
            peers=(profile,),
        )
        self.assertIn("free-form forum", prompt)
        self.assertIn("Choose your own approach", prompt)
        self.assertIn("No role or subtask has been assigned", prompt)
        self.assertIn("Do not stop", prompt)
        self.assertIn("exact @peer-name", prompt)
        self.assertIn("Unmentioned posts", prompt)
        self.assertIn("do not spend a new model call", prompt)
        self.assertIn("reply-trigger", prompt)
        self.assertIn("same thread", prompt)
        self.assertIn("@human", prompt)
        self.assertIn("web UI", prompt)
        self.assertIn("forum retire", prompt)
        self.assertNotIn("recon phase", prompt)
        self.assertNotIn("confidence score", prompt)


if __name__ == "__main__":
    unittest.main()
