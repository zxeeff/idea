from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any


class Provider(StrEnum):
    OPENAI = "openai"
    ANTHROPIC = "anthropic"


class Effort(StrEnum):
    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    XHIGH = "xhigh"
    MAX = "max"


class ProcessState(StrEnum):
    """Observable process state, not a judgement about the agent's work."""

    CREATED = "created"
    RUNNING = "running"
    DORMANT = "dormant"
    BLOCKED = "blocked"
    RETIRED = "retired"
    EXITED = "exited"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class AgentProfile:
    """Only execution-level diversity; strategy remains the agent's choice."""

    name: str
    provider: Provider
    model: str
    effort: Effort

    def __post_init__(self) -> None:
        if self.provider is Provider.ANTHROPIC and self.effort is Effort.NONE:
            raise ValueError("Claude profiles do not support effort=none")
        if not self.name.strip() or not self.model.strip():
            raise ValueError("agent name and model must be non-empty")

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["provider"] = self.provider.value
        data["effort"] = self.effort.value
        return data
