"""Bounded notification batches without changing the underlying delivery ledger."""

from __future__ import annotations

import json
import math
import sqlite3
import time
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

from .forum import Forum


@dataclass(frozen=True, slots=True)
class CommunicationPolicy:
    debounce_seconds: float = 1.0
    max_wait_seconds: float = 5.0

    def __post_init__(self) -> None:
        for name in ("debounce_seconds", "max_wait_seconds"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                raise ValueError(f"{name} must be a finite number at least zero")
        if self.max_wait_seconds < self.debounce_seconds:
            raise ValueError("max_wait_seconds must be at least debounce_seconds")


class CommunicationStore:
    DIRECT_LIMIT = 20
    THREAD_LIMIT = 20
    SUBSCRIPTION_LIMIT = 1000

    def __init__(self, forum: Forum, run_id: str):
        self.forum, self.run_id = forum, run_id
        forum.get_run(run_id)
        with forum._connection() as connection:
            connection.execute("""CREATE TABLE IF NOT EXISTS communication_policies (
                run_id TEXT PRIMARY KEY REFERENCES runs(id), policy_json TEXT NOT NULL
            )""")

    def _policy(self, connection: sqlite3.Connection) -> CommunicationPolicy:
        row = connection.execute("SELECT policy_json FROM communication_policies WHERE run_id=?", (self.run_id,)).fetchone()
        return CommunicationPolicy(**json.loads(row[0])) if row else CommunicationPolicy()

    def policy(self) -> CommunicationPolicy:
        with self.forum._connection() as connection:
            return self._policy(connection)

    def configure(self, *, debounce_seconds: float | None = None,
                  max_wait_seconds: float | None = None) -> CommunicationPolicy:
        with self.forum._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            old = self._policy(connection)
            policy = CommunicationPolicy(
                debounce_seconds=old.debounce_seconds if debounce_seconds is None else debounce_seconds,
                max_wait_seconds=old.max_wait_seconds if max_wait_seconds is None else max_wait_seconds,
            )
            connection.execute("""INSERT INTO communication_policies VALUES (?,?)
                ON CONFLICT(run_id) DO UPDATE SET policy_json=excluded.policy_json""",
                (self.run_id, json.dumps(asdict(policy))))
            return policy

    @staticmethod
    def _priority(event: dict[str, Any]) -> tuple[int, int, int]:
        human_mention = event.get("notification_reason") == "mention" and str(event.get("author", "")).casefold() in {"human", "user"}
        return int(event["priority"]), 0 if human_mention else 1, int(event.get("first_event_id", event["id"]))

    @staticmethod
    def _allocations(counts: list[int], budget: int) -> list[int]:
        """Give each selected thread a FIFO prefix before a large thread takes more."""
        allocated = [0] * len(counts)
        while budget:
            active = [index for index, count in enumerate(counts) if allocated[index] < count]
            if not active:
                break
            share = max(1, budget // len(active))
            for index in active:
                take = min(share, counts[index] - allocated[index], budget)
                allocated[index] += take
                budget -= take
                if not budget:
                    break
        return allocated

    def pending_batch(self, agent_id: str, *, immediate: bool = False,
                      now: float | None = None) -> list[dict[str, Any]]:
        current = time.time() if now is None else now
        if isinstance(current, bool) or not isinstance(current, (int, float)) or not math.isfinite(current):
            raise ValueError("now must be finite")
        if not isinstance(immediate, bool):
            raise ValueError("immediate must be a boolean")
        with self.forum._connection() as connection:
            # Policy, readiness, covered IDs and previews all use the same snapshot.
            connection.execute("BEGIN")
            agent = connection.execute("SELECT run_id FROM agents WHERE id=?", (agent_id,)).fetchone()
            if agent is None or agent["run_id"] != self.run_id:
                raise KeyError("agent does not belong to this run")
            policy = self._policy(connection)
            rows = connection.execute("""SELECT e.*,d.notification_reason,d.priority
                FROM notification_deliveries d JOIN activity e ON e.id=d.event_id
                WHERE d.agent_id=? AND d.run_id=? AND d.acknowledged_at IS NULL AND d.withdrawn_at IS NULL
                  AND d.notification_reason!='subscription'
                ORDER BY d.priority,
                    CASE WHEN d.notification_reason='mention' AND LOWER(e.author) IN ('human','user') THEN 0 ELSE 1 END,
                    d.event_id LIMIT ?""", (agent_id, self.run_id, self.DIRECT_LIMIT)).fetchall()
            direct = self.forum._activity_previews(connection, [dict(row) for row in rows])
            bypass = immediate or bool(direct)
            # Stored UTC timestamps retain microsecond boundaries. Convert only the
            # aggregate endpoints, not every event, and avoid SQLite date rounding.
            connection.create_function("communication_epoch", 1,
                                       lambda value: datetime.fromisoformat(value).timestamp(), deterministic=True)
            threads = connection.execute("""SELECT e.thread_id,COUNT(*) AS event_count,
                    MIN(d.event_id) AS first_event_id,
                    MIN(e.created_at) AS earliest_at,MAX(e.created_at) AS latest_at
                FROM notification_deliveries d JOIN activity e ON e.id=d.event_id
                WHERE d.agent_id=? AND d.run_id=? AND d.acknowledged_at IS NULL AND d.withdrawn_at IS NULL
                  AND d.notification_reason='subscription' AND e.thread_id IS NOT NULL
                GROUP BY e.thread_id
                HAVING ? OR ? >= communication_epoch(MIN(e.created_at)) + ?
                    OR ? >= communication_epoch(MAX(e.created_at)) + ?
                ORDER BY first_event_id LIMIT ?""",
                (agent_id, self.run_id, int(bypass), current, policy.max_wait_seconds,
                 current, policy.debounce_seconds, self.THREAD_LIMIT)).fetchall()
            quotas = self._allocations([int(thread["event_count"]) for thread in threads], self.SUBSCRIPTION_LIMIT)
            covered_groups = []
            for thread, quota in zip(threads, quotas):
                covered = connection.execute("""SELECT d.event_id,d.priority FROM notification_deliveries d
                    JOIN activity e ON e.id=d.event_id
                    WHERE d.agent_id=? AND d.run_id=? AND d.acknowledged_at IS NULL AND d.withdrawn_at IS NULL
                      AND d.notification_reason='subscription' AND e.thread_id=?
                    ORDER BY d.event_id LIMIT ?""", (agent_id, self.run_id, thread["thread_id"], quota)).fetchall()
                if not covered:
                    continue
                ids = [int(item["event_id"]) for item in covered]
                covered_groups.append((thread, covered, ids))
            latest_ids = [ids[-1] for _, _, ids in covered_groups]
            latest = connection.execute("SELECT * FROM activity WHERE id IN (SELECT value FROM json_each(?))",
                                        (json.dumps(latest_ids),)).fetchall()
            previews = {int(item["id"]): item for item in
                        self.forum._activity_previews(connection, [dict(row) for row in latest])}
            covered_ids = [event_id for _, _, ids in covered_groups for event_id in ids]
            relations = {int(row["event_id"]): str(row["relation"]) for row in connection.execute(
                """SELECT event_id,relation FROM comment_provenance
                   WHERE event_id IN (SELECT value FROM json_each(?)) AND relation!='reply'""",
                (json.dumps(covered_ids),)).fetchall()}
            # Carry report references even when a later ordinary comment is the
            # representative preview. Only cover IDs in this delivery snapshot.
            report_events = {int(row["event_id"]): dict(row) for row in connection.execute("""
                SELECT event_id,id AS report_id,'report' AS kind FROM approach_reports
                WHERE event_id IN (SELECT value FROM json_each(?))
                UNION ALL
                SELECT event_id,report_id,'exchange' AS kind FROM report_exchanges
                WHERE event_id IN (SELECT value FROM json_each(?))
            """, (json.dumps(covered_ids), json.dumps(covered_ids))).fetchall()}
            groups = []
            for thread, covered, ids in covered_groups:
                preview = previews[ids[-1]]
                relation_counts = dict(Counter(relations[event_id] for event_id in ids if event_id in relations))
                relation_events = [{"event_id": event_id, "relation": relations[event_id]} for event_id in ids
                                   if relations.get(event_id) in {"challenges", "retracts", "supersedes"}][:8]
                reports = [report_events[event_id] for event_id in ids if event_id in report_events]
                report_refs = reports if len(reports) <= 8 else reports[:4] + reports[-4:]
                groups.append({
                    "id": ids[-1], "run_id": self.run_id, "thread_id": thread["thread_id"],
                    "notification_reason": "subscription", "notification_mode": "passive",
                    "priority": min(int(item["priority"]) for item in covered),
                    "author": "forum", "kind": "thread_updates", "created_at": preview["created_at"],
                    "latest_author": preview["author"], "latest_kind": preview["kind"],
                    "thread_title": preview.get("thread_title"),
                    "content": f"Latest original preview (event {ids[-1]}; {len(ids)} pending updates):\n{preview.get('content') or ''}",
                    "content_truncated": bool(preview.get("content_truncated")),
                    "first_event_id": ids[0], "through_event_id": ids[-1], "event_count": len(ids),
                    "_delivery_event_ids": ids,
                    **({"provenance": preview["provenance"]} if "provenance" in preview else {}),
                    **{key: preview[key] for key in ("approach", "report", "exchange") if key in preview},
                    **({"relation_counts": relation_counts} if relation_counts else {}),
                    **({"relation_events": relation_events} if relation_events else {}),
                    **({"report_event_count": len(reports), "report_events": report_refs} if reports else {}),
                })
            return sorted(direct + groups, key=self._priority)
