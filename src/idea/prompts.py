from __future__ import annotations

import json
from typing import Iterable


_RESTART_PREAMBLE = (
    "IDEA started a fresh provider session because the previous one could not continue. "
    "The workspace and knowledge board persist; recover useful context and continue."
)


def shared_prompt(*, name: str, peer_names: Iterable[str]) -> str:
    # Keep this argument for callers without putting a large roster in every
    # provider context.
    del peer_names
    return f"""You are an independent IDEA agent named {json.dumps(name, ensure_ascii=False)}.

Work directly on the user's objective. The shared board is only a knowledge board:
read it when another agent's finding is useful, post a concise reusable finding or
limitation of your own, and reply when you have concrete information to add. Do not
coordinate work, recruit agents, tag people, follow discussions, or send status updates.
Help when needed: "$IDEA_PYTHON" -m idea forum --help
"""


def user_task(goal: str) -> str:
    return f"OBJECTIVE:\n{goal}"


def resume_task(goal: str) -> str:
    return (
        "Continue the same IDEA run. Its workspace and knowledge board persist; recover useful "
        "context from them and continue.\n\n"
        f"OBJECTIVE:\n{goal}"
    )


def blocked_restart_task(goal: str) -> str:
    return f"{_RESTART_PREAMBLE}\n\nOBJECTIVE:\n{goal}"
