from __future__ import annotations

import unittest

import tempfile
from pathlib import Path

from idea.domain import AgentProfile, Effort, Provider
from idea.profiles import (
    available_presets,
    default_profiles,
    load_profiles_file,
    parse_agent_spec,
    preset_profiles,
    resolve_profiles,
)
from idea.prompts import shared_prompt, user_task


class ProfilesAndPromptsTest(unittest.TestCase):
    def test_defaults_load_from_repo_toml_without_roles_or_phases(self) -> None:
        # The lineup is user-editable data (profiles.toml at the repository
        # root), so assert structural invariants rather than exact contents.
        profiles = default_profiles()
        self.assertGreater(len(profiles), 0)
        self.assertEqual(len(profiles), len({profile.name for profile in profiles}))
        self.assertGreater(len({profile.model for profile in profiles}), 1)
        self.assertGreater(len({profile.effort for profile in profiles}), 1)
        self.assertEqual(
            {Provider.OPENAI, Provider.ANTHROPIC},
            {profile.provider for profile in profiles},
        )
        for profile in profiles:
            self.assertFalse(hasattr(profile, "role"))
            self.assertFalse(hasattr(profile, "phase"))
            self.assertFalse(hasattr(profile, "mission"))

    def test_default_handles_expose_model_family_without_effort(self) -> None:
        self.assertEqual(
            (
                "astra-1",
                "astra-2",
                "luna-1",
                "terra-1",
                "terra-2",
                "sol-1",
                "daybreak-blue-1",
                "daybreak-blue-2",
                "sonnet-1",
                "sonnet-2",
                "sonnet-3",
                "sonnet-4",
                "opus-1",
                "opus-3",
                "opus-5",
                "opus-7",
            ),
            tuple(profile.name for profile in default_profiles()),
        )

    def test_defaults_include_astra_and_keep_providers_and_models_balanced(self) -> None:
        profiles = default_profiles()
        providers = {
            provider: [profile for profile in profiles if profile.provider is provider]
            for provider in Provider
        }
        self.assertEqual({Provider.OPENAI: 8, Provider.ANTHROPIC: 8}, {
            provider: len(items) for provider, items in providers.items()
        })
        self.assertIn("gpt-6-astra", {profile.model for profile in profiles})
        for items in providers.values():
            counts = {
                model: sum(profile.model == model for profile in items)
                for model in {profile.model for profile in items}
            }
            self.assertLessEqual(max(counts.values()) - min(counts.values()), 1)

    def test_daybreak_preset_uses_only_daybreak_at_max_effort(self) -> None:
        self.assertIn("daybreak", available_presets())
        profiles = preset_profiles("daybreak")
        self.assertEqual(16, len(profiles))
        self.assertEqual({Provider.OPENAI}, {profile.provider for profile in profiles})
        self.assertEqual({"gpt-daybreak-blue-latest"}, {profile.model for profile in profiles})
        self.assertEqual({Effort.MAX}, {profile.effort for profile in profiles})
        self.assertEqual(profiles, resolve_profiles(preset="daybreak"))
        with self.assertRaises(ValueError):
            preset_profiles("missing")

    def test_agent_spec_parses_provider_aliases_counts_and_names(self) -> None:
        profiles = parse_agent_spec("gpt:gpt-daybreak-blue-latest:high:2")
        self.assertEqual(2, len(profiles))
        self.assertEqual(Provider.OPENAI, profiles[0].provider)
        self.assertEqual("gpt-daybreak-blue-latest", profiles[0].model)
        self.assertEqual(Effort.HIGH, profiles[0].effort)
        self.assertEqual(
            ["gpt-daybreak-blue-latest-1", "gpt-daybreak-blue-latest-2"],
            [profile.name for profile in profiles],
        )
        (claude,) = parse_agent_spec("claude:opus:max")
        self.assertEqual(Provider.ANTHROPIC, claude.provider)
        for bad in ("openai:model", "nope:model:high", "openai:model:warp", "openai:model:high:0"):
            with self.assertRaises(ValueError):
                parse_agent_spec(bad)

    def test_profiles_file_and_specs_replace_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as workdir:
            path = Path(workdir) / "agents.toml"
            path.write_text(
                """
[[agents]]
name = "blue"
provider = "openai"
model = "gpt-daybreak-blue-latest"
effort = "high"
count = 2

[[agents]]
provider = "claude"
model = "opus"
effort = "max"
""",
                encoding="utf-8",
            )
            loaded = load_profiles_file(path)
            self.assertEqual(["blue", "blue-2", "opus-1"], [p.name for p in loaded])

            combined = resolve_profiles(
                specs=["openai:gpt-5.6-sol:xhigh"], profiles_file=path
            )
            self.assertEqual(4, len(combined))
            self.assertEqual("gpt-5-6-sol-1", combined[-1].name)

            filtered = resolve_profiles(names=["blue-2"], profiles_file=path)
            self.assertEqual(("blue-2",), tuple(p.name for p in filtered))

        self.assertEqual(default_profiles(), resolve_profiles())
        with self.assertRaises(ValueError):
            resolve_profiles(names=["missing"], specs=["openai:gpt-5.6-sol:high"])

    def test_top_level_prompt_is_minimal_without_exposing_provider_settings(self) -> None:
        profile = AgentProfile("peer-one", Provider.OPENAI, "hidden-self-model", Effort.LOW)
        peer = AgentProfile("peer-two", Provider.ANTHROPIC, "hidden-peer-model", Effort.MAX)
        prompt = shared_prompt(
            name=profile.name,
            peer_names=(profile.name, peer.name),
        )
        self.assertLess(len(prompt.encode("utf-8")), 800)
        self.assertIn('"peer-one"', prompt)
        self.assertNotIn('"peer-two"', prompt)
        self.assertIn("forum --help", prompt)
        self.assertIn("independent IDEA agent", prompt)
        self.assertIn("your own judgment", prompt)
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
        self.assertLess(len(default_prompt.encode("utf-8")), 800)

    def test_user_goal_is_sent_once_at_user_level(self) -> None:
        goal = "--inspect the parser for boundary bugs"
        task = user_task(goal)
        self.assertEqual(f"OBJECTIVE:\n{goal}", task)
        self.assertEqual(1, task.count(goal))


if __name__ == "__main__":
    unittest.main()
