#!/usr/bin/env python3
"""Replay one queued subscription burst through two prompt-delivery paths.

Uses only temporary local storage and the standard library. No provider is run.
The reported bytes cover wake_task output, including its repeated objective;
they exclude system/session framing and any later reads of original messages.
This measures transport representation, not model speed, tokens, or quality.

Run from any directory: python3 scripts/benchmark_communication.py --events 500
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Callable


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from idea.communication import CommunicationStore
from idea.domain import AgentProfile, Effort, Provider
from idea.forum import Forum
from idea.prompts import select_wake_context, wake_task


GOAL = "Improve the shared implementation using independently reported evidence."


def positive_integer(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("events must be at least 1")
    return number


def drain(
    forum: Forum, agent_id: str, thread_id: str,
    originals: dict[str, str], next_batch: Callable[[], list[dict]],
) -> dict[str, int]:
    batches = prompt_bytes = 0
    acknowledged: set[int] = set()
    while batch := next_batch():
        selected, background, overflow = select_wake_context(batch, [])
        if not selected:
            raise RuntimeError("delivery made no progress")
        prompt_bytes += len(wake_task(GOAL, selected, background, overflow=overflow).encode("utf-8"))
        ids = [int(event_id) for item in selected
               for event_id in item.get("_delivery_event_ids", [item["id"]])]
        if not ids or len(ids) != len(set(ids)) or acknowledged.intersection(ids):
            raise RuntimeError("delivery duplicated an event or made no progress")
        # Simulate successful delivery only. This is not a provider invocation
        # and does not claim the receiver read or understood the covered sources.
        forum.acknowledge_notifications(agent_id, ids)
        acknowledged.update(ids)
        batches += 1
    with forum._connection() as connection:
        pending = int(connection.execute(
            "SELECT COUNT(*) FROM notification_deliveries WHERE agent_id = ? "
            "AND acknowledged_at IS NULL AND withdrawn_at IS NULL", (agent_id,),
        ).fetchone()[0])
        preserved = {row["id"]: row["body"] for row in connection.execute(
            "SELECT id, body FROM comments WHERE thread_id = ?", (thread_id,),
        ).fetchall()}
    if pending or len(acknowledged) != len(originals) or preserved != originals:
        raise RuntimeError("replay lost delivery coverage or changed original messages")
    return {"delivery_batches": batches, "prompt_utf8_bytes": prompt_bytes,
            "preserved_originals": len(preserved), "pending_events": pending}


def benchmark(events: int) -> dict:
    with tempfile.TemporaryDirectory(prefix="idea-communication-benchmark-") as directory:
        root = Path(directory)
        forum = Forum(root / "state")
        run = forum.create_run(GOAL, root)
        recipients = [forum.register_agent(
            run["id"], AgentProfile(name, Provider.OPENAI, "unused", Effort.LOW)
        ) for name in ("legacy-recipient", "grouped-recipient")]
        topic = forum.create_thread(run["id"], "writer", "Shared observations", "Initial context")
        for recipient in recipients:
            forum.subscribe(recipient["id"], topic["id"], wake=True)
        originals = {}
        for number in range(1, events + 1):
            comment = forum.add_comment(
                topic["id"], "writer", f"Observation {number:06d}: the local check returned the expected result."
            )
            originals[comment["id"]] = comment["body"]
        store = CommunicationStore(forum, run["id"])
        # Both recipients see the same IDs, message bodies, objective, and empty
        # background digest. All events are queued before either replay begins.
        legacy = drain(forum, recipients[0]["id"], topic["id"], originals,
                       lambda: forum.pending_notifications(recipients[0]["id"], limit=20))
        grouped = drain(forum, recipients[1]["id"], topic["id"], originals,
                        lambda: store.pending_batch(recipients[1]["id"], immediate=True))
        return {
            "benchmark": "subscription_transport", "events": events, "threads": 1,
            "objective": GOAL, "model_calls": 0,
            "scenario": "All comments queued before delivery; empty background digest; successful delivery assumed.",
            "byte_scope": "UTF-8 wake_task output including objective; excludes system/session framing and additional original-source reads.",
            "legacy_emulation": legacy, "grouped": grouped,
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--events", type=positive_integer, default=500,
                        help="number of short subscription comments (default: 500)")
    arguments = parser.parse_args()
    print(json.dumps(benchmark(arguments.events), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
