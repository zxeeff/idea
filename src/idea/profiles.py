from __future__ import annotations

from .domain import AgentProfile, Effort, Provider


def default_profiles() -> tuple[AgentProfile, ...]:
    """Execution diversity without assigning strategies or responsibilities."""

    return (
        AgentProfile(
            name="luna-low",
            provider=Provider.OPENAI,
            model="gpt-5.6-luna",
            effort=Effort.LOW,
        ),
        AgentProfile(
            name="luna-medium",
            provider=Provider.OPENAI,
            model="gpt-5.6-luna",
            effort=Effort.MEDIUM,
        ),
        AgentProfile(
            name="terra-medium",
            provider=Provider.OPENAI,
            model="gpt-5.6-terra",
            effort=Effort.MEDIUM,
        ),
        AgentProfile(
            name="terra-high",
            provider=Provider.OPENAI,
            model="gpt-5.6-terra",
            effort=Effort.HIGH,
        ),
        AgentProfile(
            name="sol-high",
            provider=Provider.OPENAI,
            model="gpt-5.6-sol",
            effort=Effort.HIGH,
        ),
        AgentProfile(
            name="sol-xhigh",
            provider=Provider.OPENAI,
            model="gpt-5.6-sol",
            effort=Effort.XHIGH,
        ),
        AgentProfile(
            name="sol-max",
            provider=Provider.OPENAI,
            model="gpt-5.6-sol",
            effort=Effort.MAX,
        ),
        AgentProfile(
            name="sol-max-2",
            provider=Provider.OPENAI,
            model="gpt-5.6-sol",
            effort=Effort.MAX,
        ),
        AgentProfile(
            name="sonnet-low",
            provider=Provider.ANTHROPIC,
            model="sonnet",
            effort=Effort.LOW,
        ),
        AgentProfile(
            name="sonnet-high",
            provider=Provider.ANTHROPIC,
            model="sonnet",
            effort=Effort.HIGH,
        ),
        AgentProfile(
            name="opus-high",
            provider=Provider.ANTHROPIC,
            model="opus",
            effort=Effort.HIGH,
        ),
        AgentProfile(
            name="opus-high-2",
            provider=Provider.ANTHROPIC,
            model="opus",
            effort=Effort.HIGH,
        ),
        AgentProfile(
            name="opus-xhigh",
            provider=Provider.ANTHROPIC,
            model="opus",
            effort=Effort.XHIGH,
        ),
        AgentProfile(
            name="opus-xhigh-2",
            provider=Provider.ANTHROPIC,
            model="opus",
            effort=Effort.XHIGH,
        ),
        AgentProfile(
            name="opus-max",
            provider=Provider.ANTHROPIC,
            model="opus",
            effort=Effort.MAX,
        ),
        AgentProfile(
            name="opus-max-2",
            provider=Provider.ANTHROPIC,
            model="opus",
            effort=Effort.MAX,
        ),
    )


def select_profiles(names: list[str] | None = None) -> tuple[AgentProfile, ...]:
    profiles = default_profiles()
    if not names:
        return profiles
    wanted = set(names)
    selected = tuple(profile for profile in profiles if profile.name in wanted)
    missing = wanted - {profile.name for profile in selected}
    if missing:
        raise ValueError(f"unknown profiles: {', '.join(sorted(missing))}")
    return selected
