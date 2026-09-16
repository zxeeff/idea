from __future__ import annotations

import re
import tomllib
from functools import cache
from pathlib import Path
from typing import Iterable

from .domain import AgentProfile, Effort, Provider


_PROVIDER_ALIASES = {
    "openai": Provider.OPENAI,
    "gpt": Provider.OPENAI,
    "codex": Provider.OPENAI,
    "anthropic": Provider.ANTHROPIC,
    "claude": Provider.ANTHROPIC,
}
_MAX_COPIES = 500


_DEFAULTS_PATH = Path(__file__).resolve().parents[2] / "profiles.toml"
_PRESETS_PATH = Path(__file__).with_name("profile_presets")


@cache
def default_profiles() -> tuple[AgentProfile, ...]:
    """Default lineup, defined by profiles.toml at the repository root.

    Execution diversity without assigning strategies or responsibilities.
    """

    if not _DEFAULTS_PATH.is_file():
        raise FileNotFoundError(
            f"default lineup {_DEFAULTS_PATH} not found; pass --config "
            "or set IDEA_CONFIG (expected when idea is installed "
            "without its source checkout)"
        )
    return load_profiles_file(_DEFAULTS_PATH)


def available_presets() -> tuple[str, ...]:
    """Return packaged profile presets in stable CLI order."""

    if not _PRESETS_PATH.is_dir():
        return ()
    return tuple(sorted(path.stem for path in _PRESETS_PATH.glob("*.toml") if path.is_file()))


def preset_profiles(name: str) -> tuple[AgentProfile, ...]:
    """Load a packaged profile preset by its short name."""

    if name not in available_presets():
        choices = ", ".join(available_presets()) or "none installed"
        raise ValueError(f"unknown profile preset {name!r}; use one of: {choices}")
    return load_profiles_file(_PRESETS_PATH / f"{name}.toml")


def _parse_provider(value: str) -> Provider:
    try:
        return _PROVIDER_ALIASES[value.strip().lower()]
    except KeyError:
        aliases = ", ".join(sorted(_PROVIDER_ALIASES))
        raise ValueError(f"unknown provider {value!r}; use one of: {aliases}") from None


def _parse_effort(value: str) -> Effort:
    try:
        return Effort(value.strip().lower())
    except ValueError:
        efforts = ", ".join(effort.value for effort in Effort)
        raise ValueError(f"unknown effort {value!r}; use one of: {efforts}") from None


def _parse_count(value: object, context: str) -> int:
    count = int(str(value))
    if count < 1 or count > _MAX_COPIES:
        raise ValueError(f"{context}: count must be between 1 and {_MAX_COPIES}")
    return count


def _model_slug(model: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", model.lower()).strip("-") or "agent"


def _next_free_name(base: str, taken: set[str]) -> str:
    counter = 1
    name = f"{base}-{counter}"
    while name in taken:
        counter += 1
        name = f"{base}-{counter}"
    taken.add(name)
    return name


def load_profiles_file(path: str | Path) -> tuple[AgentProfile, ...]:
    """Load agent profiles from a TOML file with ``[[agents]]`` entries.

    Each entry needs ``provider``, ``model``, and ``effort``; ``name`` and
    ``count`` are optional. Unnamed entries use the model slug plus a numeric
    slot; explicitly named copies beyond the first get a ``-2``/``-3`` suffix.
    """

    source = Path(path)
    data = tomllib.loads(source.read_text(encoding="utf-8"))
    entries = data.get("agents")
    if not isinstance(entries, list) or not entries:
        raise ValueError(f"{source}: expected at least one [[agents]] entry")
    taken: set[str] = set()
    profiles: list[AgentProfile] = []
    for index, entry in enumerate(entries, start=1):
        context = f"{source}: [[agents]] #{index}"
        if not isinstance(entry, dict):
            raise ValueError(f"{context} must be a table")
        unknown = set(entry) - {"name", "provider", "model", "effort", "count"}
        if unknown:
            raise ValueError(f"{context} has unknown keys: {', '.join(sorted(unknown))}")
        missing = {"provider", "model", "effort"} - set(entry)
        if missing:
            raise ValueError(f"{context} is missing: {', '.join(sorted(missing))}")
        provider = _parse_provider(str(entry["provider"]))
        model = str(entry["model"])
        effort = _parse_effort(str(entry["effort"]))
        count = _parse_count(entry.get("count", 1), context)
        explicit = str(entry.get("name", "")).strip()
        for copy in range(count):
            if explicit:
                name = explicit if copy == 0 else f"{explicit}-{copy + 1}"
                if name in taken:
                    raise ValueError(f"{context}: duplicate agent name {name!r}")
                taken.add(name)
            else:
                name = _next_free_name(_model_slug(model), taken)
            profiles.append(
                AgentProfile(name=name, provider=provider, model=model, effort=effort)
            )
    return tuple(profiles)


def _filter_by_names(
    profiles: tuple[AgentProfile, ...], names: Iterable[str] | None
) -> tuple[AgentProfile, ...]:
    names = list(names or [])
    if not names:
        return profiles
    wanted = set(names)
    selected = tuple(profile for profile in profiles if profile.name in wanted)
    missing = wanted - {profile.name for profile in selected}
    if missing:
        raise ValueError(f"unknown profiles: {', '.join(sorted(missing))}")
    return selected


def resolve_profiles(
    names: Iterable[str] | None = None,
    profiles_file: str | Path | None = None,
    preset: str | None = None,
) -> tuple[AgentProfile, ...]:
    """Build the active profile set.

    A packaged preset or TOML configuration replaces the built-in defaults;
    with neither, the defaults apply. A preset and configuration are mutually
    exclusive. ``names`` then filters by profile name.
    """

    if preset and profiles_file:
        raise ValueError("a profile preset and profiles file cannot be used together")
    if preset or profiles_file:
        profiles = preset_profiles(preset) if preset else load_profiles_file(profiles_file)
    else:
        profiles = default_profiles()
    return _filter_by_names(profiles, names)


def select_profiles(names: list[str] | None = None) -> tuple[AgentProfile, ...]:
    return _filter_by_names(default_profiles(), names)
