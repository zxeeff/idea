"""Forum participation and shared admission accounting, without assigning work."""
from __future__ import annotations

import json
import math
import sqlite3
import time
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any, Iterable

from .domain import AgentProfile, ProcessState
from .forum import Forum, _id, _now

if TYPE_CHECKING:
    from .execution import ExecutionPolicy


@dataclass(frozen=True, slots=True)
class PopulationPolicy:
    initial_agents: int = 16
    max_agents: int = 100
    birth_burst: int = 16
    births_per_minute: float = 2
    max_births: int = 500
    max_invocations: int = 5000
    participation_grace: float = 30
    call_ttl: float = 1800
    max_open_calls_per_agent: int = 4
    max_offers_per_call: int = 2
    offer_cooldown: float = 300
    idle_timeout: float = 300

    def __post_init__(self) -> None:
        for name in ("initial_agents", "max_agents", "birth_burst", "max_births", "max_invocations",
                     "max_open_calls_per_agent", "max_offers_per_call"):
            value = getattr(self, name)
            maximum = 10_000_000 if name in {"max_births", "max_invocations"} else 500
            minimum = 0 if name == "max_offers_per_call" else 1
            if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
                raise ValueError(f"{name} must be an integer between {minimum} and {maximum}")
        for name in ("births_per_minute", "participation_grace", "call_ttl", "offer_cooldown", "idle_timeout"):
            value = getattr(self, name)
            minimum = 0 if name in {"participation_grace", "offer_cooldown"} else 0.000001
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < minimum:
                raise ValueError(f"{name} must be a finite number {'at least zero' if minimum == 0 else 'greater than zero'}")
        if self.initial_agents > self.max_agents:
            raise ValueError("initial_agents cannot exceed max_agents")
        if self.participation_grace >= self.call_ttl:
            raise ValueError("participation_grace must be shorter than call_ttl")


class PopulationStore:
    def __init__(self, forum: Forum, run_id: str):
        self.forum, self.run_id = forum, run_id
        forum.get_run(run_id)
        with forum._connection() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS population_config (
                    run_id TEXT PRIMARY KEY REFERENCES runs(id), policy_json TEXT NOT NULL,
                    enabled INTEGER NOT NULL CHECK(enabled IN (0,1))
                );
                CREATE TABLE IF NOT EXISTS population_state (
                    run_id TEXT PRIMARY KEY REFERENCES runs(id), bucket_tokens REAL NOT NULL,
                    bucket_updated_at REAL NOT NULL, total_births INTEGER NOT NULL DEFAULT 0,
                    invocations_started INTEGER NOT NULL DEFAULT 0, initial_reserved INTEGER NOT NULL DEFAULT 0,
                    template_cursor INTEGER NOT NULL DEFAULT 0, adopted_agents INTEGER NOT NULL DEFAULT 0,
                    adopted_known_starts INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS agent_templates (
                    id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id),
                    provider TEXT NOT NULL, model TEXT NOT NULL, effort TEXT NOT NULL,
                    position INTEGER NOT NULL, enabled INTEGER NOT NULL DEFAULT 1,
                    UNIQUE(run_id,provider,model,effort)
                );
                CREATE TABLE IF NOT EXISTS participation_calls (
                    id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id),
                    requester_agent_id TEXT NOT NULL REFERENCES agents(id),
                    thread_id TEXT NOT NULL REFERENCES threads(id), reason TEXT NOT NULL,
                    template_id TEXT REFERENCES agent_templates(id), state TEXT NOT NULL,
                    created_at REAL NOT NULL, expires_at REAL NOT NULL, request_key TEXT,
                    fulfilled_by TEXT REFERENCES agents(id), fulfillment_kind TEXT, birth_id TEXT,
                    UNIQUE(run_id,requester_agent_id,request_key)
                );
                CREATE INDEX IF NOT EXISTS participation_calls_by_run
                    ON participation_calls(run_id,state,created_at,id);
                CREATE TABLE IF NOT EXISTS call_participations (
                    call_id TEXT NOT NULL REFERENCES participation_calls(id),
                    agent_id TEXT NOT NULL REFERENCES agents(id), created_at REAL NOT NULL,
                    PRIMARY KEY(call_id,agent_id)
                );
                CREATE TABLE IF NOT EXISTS population_births (
                    birth_id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id),
                    agent_id TEXT NOT NULL UNIQUE REFERENCES agents(id),
                    template_id TEXT NOT NULL REFERENCES agent_templates(id),
                    call_id TEXT UNIQUE REFERENCES participation_calls(id), kind TEXT NOT NULL,
                    state TEXT NOT NULL, prepaid INTEGER NOT NULL, invocation_started INTEGER NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL, finished_at REAL, error TEXT
                );
                CREATE INDEX IF NOT EXISTS population_births_by_run
                    ON population_births(run_id,state,created_at);
                CREATE INDEX IF NOT EXISTS calls_by_requester
                    ON participation_calls(run_id,requester_agent_id,state);
                CREATE TABLE IF NOT EXISTS participation_offers (
                    id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id),
                    call_id TEXT NOT NULL REFERENCES participation_calls(id),
                    agent_id TEXT NOT NULL REFERENCES agents(id),
                    event_id INTEGER NOT NULL REFERENCES activity(id),
                    created_at REAL NOT NULL, state TEXT NOT NULL DEFAULT 'pending',
                    closed_at REAL, UNIQUE(call_id,agent_id)
                );
                CREATE UNIQUE INDEX IF NOT EXISTS one_pending_offer_per_call
                    ON participation_offers(call_id) WHERE state='pending';
                CREATE UNIQUE INDEX IF NOT EXISTS one_pending_offer_per_peer
                    ON participation_offers(agent_id) WHERE state='pending';
                CREATE INDEX IF NOT EXISTS offers_by_agent
                    ON participation_offers(agent_id,created_at);
                CREATE TABLE IF NOT EXISTS population_admissions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL REFERENCES runs(id),
                    requester_agent_id TEXT NOT NULL REFERENCES agents(id),
                    call_id TEXT NOT NULL REFERENCES participation_calls(id),
                    kind TEXT NOT NULL, subject_id TEXT NOT NULL UNIQUE, created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS admissions_by_requester
                    ON population_admissions(run_id,requester_agent_id,id);
            """)
            connection.execute("BEGIN IMMEDIATE")
            marker = f"population-admissions-v1:{self.run_id}"
            if connection.execute("SELECT 1 FROM forum_migrations WHERE name=?", (marker,)).fetchone() is None:
                connection.execute("""INSERT OR IGNORE INTO population_admissions
                    (run_id,requester_agent_id,call_id,kind,subject_id,created_at)
                    SELECT b.run_id,c.requester_agent_id,c.id,'birth',b.birth_id,b.created_at
                    FROM population_births b JOIN participation_calls c ON c.id=b.call_id
                    WHERE b.run_id=? ORDER BY b.created_at,b.birth_id""", (self.run_id,))
                connection.execute("INSERT INTO forum_migrations VALUES (?)", (marker,))

    def configured(self) -> bool:
        with self.forum._connection() as connection:
            return connection.execute("SELECT 1 FROM population_config WHERE run_id=?", (self.run_id,)).fetchone() is not None

    def policy(self) -> PopulationPolicy | None:
        with self.forum._connection() as connection:
            row = connection.execute("SELECT policy_json FROM population_config WHERE run_id=?", (self.run_id,)).fetchone()
        return PopulationPolicy(**json.loads(row[0])) if row else None

    @property
    def enabled(self) -> bool:
        with self.forum._connection() as connection:
            row = connection.execute("SELECT enabled FROM population_config WHERE run_id=?", (self.run_id,)).fetchone()
        return bool(row[0]) if row else False

    @staticmethod
    def _policy(connection: sqlite3.Connection, run_id: str) -> PopulationPolicy:
        row = connection.execute("SELECT policy_json FROM population_config WHERE run_id=?", (run_id,)).fetchone()
        if row is None:
            raise RuntimeError("population policy has not been configured")
        return PopulationPolicy(**json.loads(row[0]))

    def _agent(self, connection: sqlite3.Connection, agent_id: str, *, allow_retired: bool = False) -> sqlite3.Row:
        row = connection.execute("SELECT * FROM agents WHERE id=? AND run_id=?", (agent_id, self.run_id)).fetchone()
        if row is None:
            raise KeyError("agent does not belong to this run")
        if not allow_retired and row["process_state"] == "retired":
            raise ValueError("a retired agent cannot request or join participation")
        return row

    def _template(self, connection: sqlite3.Connection, provider: str, model: str, effort: str, position: int) -> str:
        row = connection.execute("SELECT id FROM agent_templates WHERE run_id=? AND provider=? AND model=? AND effort=?",
                                 (self.run_id, provider, model, effort)).fetchone()
        identifier = str(row[0]) if row else _id("template")
        connection.execute("""INSERT INTO agent_templates VALUES (?,?,?,?,?,?,1)
            ON CONFLICT(run_id,provider,model,effort) DO UPDATE SET position=excluded.position,enabled=1""",
            (identifier, self.run_id, provider, model, effort, position))
        return identifier

    def configure(self, profiles: Iterable[AgentProfile] | None = None,
                  policy: PopulationPolicy | None = None, *, enabled: bool | None = None) -> PopulationPolicy:
        if policy is not None and not isinstance(policy, PopulationPolicy):
            raise ValueError("policy must be a PopulationPolicy")
        if enabled is not None and not isinstance(enabled, bool):
            raise ValueError("enabled must be a boolean")
        provided = None if profiles is None else list(profiles)
        now = time.time()
        with self.forum._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            previous = connection.execute("SELECT * FROM population_config WHERE run_id=?", (self.run_id,)).fetchone()
            selected = policy or (PopulationPolicy(**json.loads(previous["policy_json"])) if previous else PopulationPolicy())
            active = enabled if enabled is not None else (bool(previous["enabled"]) if previous else True)
            if previous:
                self._refill(connection, now)
            connection.execute("""INSERT INTO population_config VALUES (?,?,?) ON CONFLICT(run_id)
                DO UPDATE SET policy_json=excluded.policy_json,enabled=excluded.enabled""",
                (self.run_id, json.dumps(asdict(selected)), int(active)))
            connection.execute("INSERT OR IGNORE INTO population_state(run_id,bucket_tokens,bucket_updated_at) VALUES (?,?,?)",
                               (self.run_id, selected.birth_burst, now))
            connection.execute("UPDATE population_state SET bucket_tokens=MIN(bucket_tokens,?) WHERE run_id=?",
                               (selected.birth_burst, self.run_id))
            existing = connection.execute("SELECT * FROM agents WHERE run_id=? ORDER BY created_at,id", (self.run_id,)).fetchall()
            recorded_attempts: dict[str, int] = {}
            if connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='execution_attempts'").fetchone():
                recorded_attempts = {str(row["agent_id"]): int(row["n"]) for row in connection.execute(
                    """SELECT r.agent_id,COUNT(*) AS n FROM execution_attempts t
                       JOIN execution_requests r ON r.id=t.request_id
                       WHERE r.run_id=? GROUP BY r.agent_id""", (self.run_id,)).fetchall()}
            if provided is None and not previous:
                signatures = [(str(item["provider"]), str(item["model"]), str(item["effort"])) for item in existing]
            elif provided is not None:
                signatures = [(item.provider.value, item.model, item.effort.value) for item in provided]
            else:
                signatures = None
            if signatures is not None:
                signatures = list(dict.fromkeys(signatures))
                if not signatures:
                    raise ValueError("at least one allowed agent template is required")
                # Interleave providers while preserving order within each provider.
                buckets: dict[str, list[tuple[str, str, str]]] = {}
                for signature in signatures:
                    buckets.setdefault(signature[0], []).append(signature)
                ordered: list[tuple[str, str, str]] = []
                while any(buckets.values()):
                    for candidates in buckets.values():
                        if candidates:
                            ordered.append(candidates.pop(0))
                connection.execute("UPDATE agent_templates SET enabled=0 WHERE run_id=?", (self.run_id,))
                for position, signature in enumerate(ordered):
                    self._template(connection, *signature, position)
            for item in existing:
                if connection.execute("SELECT 1 FROM population_births WHERE agent_id=?", (item["id"],)).fetchone():
                    continue
                match = connection.execute("SELECT id FROM agent_templates WHERE run_id=? AND provider=? AND model=? AND effort=?",
                                           (self.run_id, item["provider"], item["model"], item["effort"])).fetchone()
                if match is None:
                    identifier = _id("template")
                    connection.execute("INSERT INTO agent_templates VALUES (?,?,?,?,?,?,0)",
                                       (identifier, self.run_id, item["provider"], item["model"], item["effort"], 1_000_000))
                else:
                    identifier = str(match[0])
                prior_attempts = recorded_attempts.get(str(item["id"]), 0)
                known = bool(item["session_id"] or item["started_at"] or item["process_state"] == "running" or prior_attempts)
                connection.execute("""INSERT INTO population_births
                    (birth_id,run_id,agent_id,template_id,kind,state,prepaid,invocation_started,created_at)
                    VALUES (?,?,?,?, 'adopted','adopted',0,?,?)""",
                    (_id("birth"), self.run_id, item["id"], identifier, int(known), now))
                connection.execute("""UPDATE population_state SET adopted_agents=adopted_agents+1,
                    adopted_known_starts=adopted_known_starts+?,total_births=total_births+?,
                    invocations_started=invocations_started+?,initial_reserved=MIN(?,initial_reserved+1) WHERE run_id=?""",
                    (int(known), int(known), max(int(known), prior_attempts), selected.initial_agents, self.run_id))
            return selected

    def templates(self) -> list[dict[str, Any]]:
        with self.forum._connection() as connection:
            rows = connection.execute("SELECT * FROM agent_templates WHERE run_id=? AND enabled=1 ORDER BY position,id", (self.run_id,)).fetchall()
        return [dict(row) for row in rows]

    def _refill(self, connection: sqlite3.Connection, now: float) -> tuple[PopulationPolicy, sqlite3.Row, float]:
        policy = self._policy(connection, self.run_id)
        row = connection.execute("SELECT * FROM population_state WHERE run_id=?", (self.run_id,)).fetchone()
        elapsed = max(0.0, now - float(row["bucket_updated_at"]))
        tokens = min(float(policy.birth_burst), float(row["bucket_tokens"]) + elapsed * policy.births_per_minute / 60)
        connection.execute("UPDATE population_state SET bucket_tokens=?,bucket_updated_at=? WHERE run_id=?",
                           (tokens, max(now, float(row["bucket_updated_at"])), self.run_id))
        return policy, row, tokens

    def _expire(self, connection: sqlite3.Connection, now: float) -> None:
        connection.execute("UPDATE participation_calls SET state='expired' WHERE run_id=? AND state='open' AND expires_at<=?", (self.run_id, now))
        self._refresh_offers(connection, now)

    @staticmethod
    def _execution_available(connection: sqlite3.Connection) -> bool:
        return connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='execution_requests'").fetchone() is not None

    def _refresh_offers(self, connection: sqlite3.Connection, now: float) -> None:
        rows = connection.execute("""SELECT o.*,c.state AS call_state,c.fulfilled_by,a.process_state,
            d.acknowledged_at,d.withdrawn_at FROM participation_offers o
            JOIN participation_calls c ON c.id=o.call_id JOIN agents a ON a.id=o.agent_id
            LEFT JOIN notification_deliveries d ON d.agent_id=o.agent_id AND d.event_id=o.event_id
            WHERE o.run_id=? AND o.state='pending'""", (self.run_id,)).fetchall()
        for offer in rows:
            if offer["call_state"] == "filled":
                state = "accepted" if offer["fulfilled_by"] == offer["agent_id"] else "superseded"
            elif offer["acknowledged_at"] is not None:
                state = "acknowledged"
            elif offer["call_state"] != "open":
                state = "expired" if offer["call_state"] == "expired" else "cancelled"
            elif offer["process_state"] in {"failed", "blocked", "retired"}:
                state = "failed"
            elif offer["withdrawn_at"] is not None:
                state = "cancelled"
            else:
                continue
            connection.execute("UPDATE participation_offers SET state=?,closed_at=? WHERE id=?", (state, now, offer["id"]))
            # Withdrawal preserves the distinction between cancellation and successful delivery.
            if state != "acknowledged":
                connection.execute("""UPDATE notification_deliveries SET withdrawn_at=COALESCE(withdrawn_at,?)
                    WHERE agent_id=? AND event_id=? AND notification_reason='invitation'
                    AND acknowledged_at IS NULL""", (_now(), offer["agent_id"], offer["event_id"]))

    def _resident_count(self, connection: sqlite3.Connection) -> int:
        return int(connection.execute("""SELECT COUNT(*) FROM agents WHERE run_id=?
            AND process_state!='retired' AND participation_state!='parked'""", (self.run_id,)).fetchone()[0])

    def _call(self, connection: sqlite3.Connection, call_id: str) -> sqlite3.Row:
        row = connection.execute("SELECT * FROM participation_calls WHERE id=? AND run_id=?", (call_id, self.run_id)).fetchone()
        if row is None:
            raise KeyError("participation call does not belong to this run")
        return row

    def _announce(
        self,
        connection: sqlite3.Connection,
        agent: sqlite3.Row,
        thread_id: str,
        body: str,
        *,
        kind: str,
    ) -> None:
        comment_id = _id("comment")
        connection.execute("INSERT INTO comments VALUES (?,?,?,?,?)", (comment_id, thread_id, agent["name"], body, _now()))
        connection.execute(
            "INSERT INTO comment_presentation(comment_id,mode,kind) VALUES (?,'coordination',?)",
            (comment_id, kind),
        )
        # Participation announcements reach thread subscribers; text is not a broadcast command.
        self.forum._record_activity(connection, run_id=self.run_id, author=str(agent["name"]),
                                    kind="comment", subject_id=comment_id, thread_id=thread_id, text="Participation update")
        connection.execute("INSERT OR IGNORE INTO thread_subscriptions VALUES (?,?,0,?)", (agent["id"], thread_id, _now()))

    def open_call(self, agent_id: str, thread_id: str, reason: str, *, template_id: str | None = None,
                  ttl: float | None = None, request_key: str | None = None) -> dict[str, Any]:
        if not isinstance(reason, str) or not reason.strip() or len(reason.encode("utf-8")) > 16_384:
            raise ValueError("participation reason must contain 1 to 16384 UTF-8 bytes")
        if request_key is not None and (not isinstance(request_key, str) or not request_key or len(request_key) > 200):
            raise ValueError("request_key must contain 1 to 200 characters")
        now = time.time()
        with self.forum._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            policy = self._policy(connection, self.run_id)
            self._expire(connection, now)
            agent = self._agent(connection, agent_id)
            thread = connection.execute("SELECT 1 FROM threads WHERE id=? AND run_id=?", (thread_id, self.run_id)).fetchone()
            if thread is None:
                raise ValueError("participation thread must belong to this run")
            duration = policy.call_ttl if ttl is None else ttl
            if isinstance(duration, bool) or not isinstance(duration, (int,float)) or not math.isfinite(duration) or not 0 < duration <= policy.call_ttl:
                raise ValueError("ttl must be positive and no longer than call_ttl")
            if template_id is not None and connection.execute("SELECT 1 FROM agent_templates WHERE id=? AND run_id=? AND enabled=1", (template_id, self.run_id)).fetchone() is None:
                raise ValueError("template must be enabled for this run")
            if request_key is not None:
                previous = connection.execute("SELECT * FROM participation_calls WHERE run_id=? AND requester_agent_id=? AND request_key=?", (self.run_id, agent_id, request_key)).fetchone()
                if previous:
                    if (previous["thread_id"], previous["reason"], previous["template_id"]) != (thread_id, reason.strip(), template_id):
                        raise ValueError("request_key already identifies a different participation call")
                    return dict(previous)
            open_count = connection.execute("""SELECT COUNT(*) FROM participation_calls
                WHERE run_id=? AND requester_agent_id=? AND state='open'""", (self.run_id, agent_id)).fetchone()[0]
            if open_count >= policy.max_open_calls_per_agent:
                raise ValueError("this peer has reached max_open_calls_per_agent; cancel an obsolete invitation first")
            identifier = _id("call")
            connection.execute("""INSERT INTO participation_calls
                (id,run_id,requester_agent_id,thread_id,reason,template_id,state,created_at,expires_at,request_key)
                VALUES (?,?,?,?,?,?,'open',?,?,?)""",
                (identifier, self.run_id, agent_id, thread_id, reason.strip(), template_id, now, now + duration, request_key))
            self._announce(
                connection,
                agent,
                thread_id,
                f"Open participation request {identifier}\n\n{reason.strip()}",
                kind="participation_call",
            )
            return dict(self._call(connection, identifier))

    def get_call(self, call_id: str) -> dict[str, Any]:
        with self.forum._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._expire(connection, time.time())
            return dict(self._call(connection, call_id))

    def list_calls(self, *, state: str = "open", limit: int = 30, after: str | None = None) -> dict[str, Any]:
        self.forum._page_limit(limit)
        if state not in {"open", "filled", "cancelled", "expired", "failed", "all"}:
            raise ValueError("unknown participation call state")
        with self.forum._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._expire(connection, time.time())
            values: list[Any] = [self.run_id]
            clauses = ["run_id=?"]
            if state != "all":
                clauses.append("state=?")
                values.append(state)
            if after:
                cursor = self._call(connection, str(after))
                clauses.append("(created_at>? OR (created_at=? AND id>?))")
                values.extend((cursor["created_at"], cursor["created_at"], cursor["id"]))
            rows = connection.execute(f"SELECT * FROM participation_calls WHERE {' AND '.join(clauses)} ORDER BY created_at,id LIMIT ?", (*values, limit + 1)).fetchall()
        items = [dict(row) for row in rows[:limit]]
        return {"items": items, "next_cursor": items[-1]["id"] if len(rows) > limit else None}

    def volunteer(self, agent_id: str, call_id: str) -> dict[str, Any]:
        now = time.time()
        with self.forum._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._expire(connection, now)
            agent = self._agent(connection, agent_id)
            call = self._call(connection, call_id)
            if call["state"] in {"cancelled", "expired", "failed"}:
                raise ValueError("participation call is no longer open")
            if agent_id == call["requester_agent_id"]:
                raise ValueError("use cancel_call to withdraw your own request")
            inserted = connection.execute("INSERT OR IGNORE INTO call_participations VALUES (?,?,?)", (call_id, agent_id, now))
            if call["state"] == "open":
                connection.execute("UPDATE participation_calls SET state='filled',fulfilled_by=?,fulfillment_kind='volunteer' WHERE id=?", (agent_id, call_id))
            if inserted.rowcount:
                self._announce(
                    connection,
                    agent,
                    str(call["thread_id"]),
                    f"Volunteered to consider participation request {call_id}.",
                    kind="participation_status",
                )
            self._refresh_offers(connection, now)
            return dict(self._call(connection, call_id))

    def cancel_call(self, agent_id: str, call_id: str) -> dict[str, Any]:
        with self.forum._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._expire(connection, time.time())
            agent = self._agent(connection, agent_id, allow_retired=True)
            call = self._call(connection, call_id)
            if agent_id != call["requester_agent_id"]:
                raise ValueError("only the requesting peer can withdraw its call")
            if call["state"] == "filled":
                raise ValueError("participation has already been admitted")
            if call["state"] == "open":
                connection.execute("UPDATE participation_calls SET state='cancelled' WHERE id=?", (call_id,))
                self._announce(
                    connection,
                    agent,
                    str(call["thread_id"]),
                    f"Withdrew participation request {call_id}.",
                    kind="participation_status",
                )
            self._refresh_offers(connection, time.time())
            return dict(self._call(connection, call_id))

    def _provider_room(self, connection: sqlite3.Connection,
                       execution_policy: ExecutionPolicy | None) -> dict[str, bool]:
        providers = ("openai", "anthropic")
        if execution_policy is None:
            return dict.fromkeys(providers, True)
        running = dict.fromkeys(providers, 0)
        queued = dict.fromkeys(providers, 0)
        accounted: set[str] = set()
        if self._execution_available(connection):
            for row in connection.execute("""SELECT r.agent_id,r.state,a.provider FROM execution_requests r
                JOIN agents a ON a.id=r.agent_id WHERE r.run_id=? AND r.state IN ('running','queued')
                AND (r.state='running' OR a.process_state!='retired')""", (self.run_id,)):
                accounted.add(str(row["agent_id"]))
                counts = running if row["state"] == "running" else queued
                counts[str(row["provider"])] += 1
        # Account for durable admissions not yet enqueued, without double-counting them.
        reserved = connection.execute("""SELECT b.agent_id,a.provider FROM population_births b
            JOIN agents a ON a.id=b.agent_id WHERE b.run_id=? AND b.state IN ('reserved','ready')
            AND b.invocation_started=0 AND a.process_state!='retired'
            UNION SELECT o.agent_id,a.provider FROM participation_offers o JOIN agents a ON a.id=o.agent_id
            WHERE o.run_id=? AND o.state='pending' AND a.process_state!='retired'""", (self.run_id, self.run_id))
        for row in reserved:
            if str(row["agent_id"]) not in accounted:
                queued[str(row["provider"])] += 1
                accounted.add(str(row["agent_id"]))
        occupied = {provider: running[provider] + min(queued[provider], max(0,
                    execution_policy.provider_limit(provider) - running[provider])) for provider in providers}
        return {provider: sum(occupied.values()) < execution_policy.max_concurrent
                and occupied[provider] < execution_policy.provider_limit(provider) for provider in providers}

    def _record_admission(self, connection: sqlite3.Connection, call: sqlite3.Row,
                          kind: str, subject_id: str, now: float) -> None:
        connection.execute("""INSERT INTO population_admissions
            (run_id,requester_agent_id,call_id,kind,subject_id,created_at) VALUES (?,?,?,?,?,?)""",
            (self.run_id, call["requester_agent_id"], call["id"], kind, subject_id, now))

    def _offer_candidate(self, connection: sqlite3.Connection, call: sqlite3.Row,
                         policy: PopulationPolicy, now: float, room: dict[str, bool],
                         available: set[str] | None) -> sqlite3.Row | None:
        count = int(connection.execute("SELECT COUNT(*) FROM participation_offers WHERE call_id=?", (call["id"],)).fetchone()[0])
        if count >= policy.max_offers_per_call:
            return None
        clauses = ["a.run_id=?", "a.id!=?", "a.process_state='dormant'", "a.session_id IS NOT NULL", "a.session_id!=''",
                   "NOT EXISTS (SELECT 1 FROM participation_offers o WHERE o.agent_id=a.id AND (o.state='pending' OR o.call_id=?))",
                   "NOT EXISTS (SELECT 1 FROM notification_deliveries d WHERE d.agent_id=a.id AND d.acknowledged_at IS NULL AND d.withdrawn_at IS NULL)"]
        values: list[Any] = [self.run_id, call["requester_agent_id"], call["id"]]
        if self._execution_available(connection):
            clauses.append("NOT EXISTS (SELECT 1 FROM execution_requests r WHERE r.agent_id=a.id AND r.state IN ('queued','running'))")
        if call["template_id"]:
            clauses.append("EXISTS (SELECT 1 FROM agent_templates t WHERE t.id=? AND t.provider=a.provider AND t.model=a.model AND t.effort=a.effort)")
            values.append(call["template_id"])
        rows = connection.execute(f"""SELECT a.*, (SELECT MAX(o.created_at) FROM participation_offers o
            WHERE o.agent_id=a.id) AS last_offered FROM agents a WHERE {' AND '.join(clauses)}
            ORDER BY CASE WHEN EXISTS (
                SELECT 1 FROM approach_members m JOIN approaches p ON p.id=m.approach_id
                WHERE m.agent_id=a.id AND m.left_at IS NULL AND p.run_id=a.run_id AND p.thread_id=?
            ) THEN 0 ELSE 1 END,COALESCE(last_offered,-1e30),a.created_at,a.id""",
            (*values, call["thread_id"])).fetchall()
        resident_full = self._resident_count(connection) >= policy.max_agents
        for row in rows:
            if available is not None and str(row["id"]) not in available:
                continue
            if not room.get(str(row["provider"]), False):
                continue
            if row["participation_state"] == "parked" and resident_full:
                continue
            if row["last_offered"] is not None and float(row["last_offered"]) + policy.offer_cooldown > now:
                continue
            return row
        return None

    def _make_offer(self, connection: sqlite3.Connection, call: sqlite3.Row,
                    candidate: sqlite3.Row, now: float) -> None:
        identifier, comment_id = _id("offer"), _id("comment")
        body = (f"Optional participation invitation {call['id']} for {candidate['name']}. "
                f"Read this thread and choose whether to join with idea forum volunteer {call['id']}. "
                "You may decline by not volunteering.")
        created_at = _now()
        connection.execute("INSERT INTO comments VALUES (?,?,?,?,?)", (comment_id, call["thread_id"], "system", body, created_at))
        connection.execute(
            "INSERT INTO comment_presentation(comment_id,mode,kind) VALUES (?,'coordination','participation_offer')",
            (comment_id,),
        )
        # Insert explicitly: names/reasons containing @all cannot create a broadcast,
        # and this offer wakes one candidate rather than all thread subscribers.
        event = connection.execute("""INSERT INTO activity
            (run_id,author,kind,subject_id,thread_id,audience_json,notification_mode,created_at)
            VALUES (?,'system','comment',?,?,?,'passive',?)""",
            (self.run_id, comment_id, call["thread_id"], json.dumps([candidate["name"]]), created_at))
        event_id = int(event.lastrowid)
        connection.execute("""INSERT INTO notification_deliveries
            (agent_id,event_id,run_id,notification_reason,priority) VALUES (?,?,?,'invitation',3)""",
            (candidate["id"], event_id, self.run_id))
        connection.execute("""INSERT INTO participation_offers
            (id,run_id,call_id,agent_id,event_id,created_at) VALUES (?,?,?,?,?,?)""",
            (identifier, self.run_id, call["id"], candidate["id"], event_id, now))
        self._record_admission(connection, call, "offer", identifier, now)

    def reserve_birth(self, *, initial: bool = False, now: float | None = None,
                      execution_policy: ExecutionPolicy | None = None,
                      available_agent_ids: Iterable[str] | None = None) -> dict[str, Any] | None:
        current = time.time() if now is None else now
        if isinstance(current, bool) or not isinstance(current, (int,float)) or not math.isfinite(current):
            raise ValueError("now must be finite")
        available = None if available_agent_ids is None else set(available_agent_ids)
        with self.forum._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            config = connection.execute("SELECT enabled FROM population_config WHERE run_id=?", (self.run_id,)).fetchone()
            if config is None or not config[0]:
                return None
            policy, counters, tokens = self._refill(connection, current)
            self._expire(connection, current)
            if int(counters["invocations_started"]) >= policy.max_invocations:
                return None
            prepaid_waiting = int(connection.execute("""SELECT COUNT(*) FROM population_births b
                JOIN agents a ON a.id=b.agent_id WHERE b.run_id=? AND b.prepaid=1
                AND b.invocation_started=0 AND b.state IN ('reserved','ready')
                AND a.process_state!='retired'""", (self.run_id,)).fetchone()[0])
            if int(counters["invocations_started"]) + prepaid_waiting >= policy.max_invocations:
                return None
            room = self._provider_room(connection, execution_policy)
            templates = connection.execute("SELECT * FROM agent_templates WHERE run_id=? AND enabled=1 ORDER BY position,id", (self.run_id,)).fetchall()
            if not templates:
                return None
            call = None
            if initial:
                if int(counters["initial_reserved"]) >= policy.initial_agents:
                    return None
            else:
                calls = connection.execute("""SELECT c.* FROM participation_calls c
                    LEFT JOIN agent_templates t ON t.id=c.template_id
                    WHERE c.run_id=? AND c.state='open' AND c.expires_at>?
                      AND (c.template_id IS NULL OR t.enabled=1)
                      AND NOT EXISTS (SELECT 1 FROM participation_offers o WHERE o.call_id=c.id AND o.state='pending')
                    ORDER BY COALESCE((SELECT MAX(h.id) FROM population_admissions h
                        WHERE h.run_id=c.run_id AND h.requester_agent_id=c.requester_agent_id),0),c.created_at,c.id""",
                    (self.run_id, current)).fetchall()
                for invitation in calls:
                    candidate = self._offer_candidate(connection, invitation, policy, current, room, available)
                    if candidate is not None:
                        self._make_offer(connection, invitation, candidate, current)
                        return None
                    if float(invitation["created_at"]) + policy.participation_grace > current:
                        continue
                    if not any(room.get(str(item["provider"]), False) for item in templates
                               if not invitation["template_id"] or item["id"] == invitation["template_id"]):
                        continue
                    call = invitation
                    break
                if call is None:
                    return None
            # Existing sessions get a chance even when fresh-session tokens are depleted.
            if tokens < 1 or int(counters["total_births"]) >= policy.max_births or self._resident_count(connection) >= policy.max_agents:
                return None
            if call is not None and call["template_id"]:
                template = next(item for item in templates if item["id"] == call["template_id"])
                if not room.get(str(template["provider"]), False):
                    return None
            else:
                providers = list(dict.fromkeys(str(item["provider"]) for item in templates))
                cursor = int(counters["template_cursor"])
                provider = next((providers[(cursor + offset) % len(providers)] for offset in range(len(providers))
                                 if room.get(providers[(cursor + offset) % len(providers)], False)), None)
                if provider is None:
                    return None
                choices = [item for item in templates if item["provider"] == provider]
                template = choices[(cursor // len(providers)) % len(choices)]
            agent_id, birth_id = _id("agent"), _id("birth")
            prefix = "codex" if template["provider"] == "openai" else "claude"
            number = int(counters["total_births"]) + 1
            name = f"{prefix}-{number:04d}"
            while connection.execute("SELECT 1 FROM agents WHERE run_id=? AND name=?", (self.run_id, name)).fetchone():
                number += 1
                name = f"{prefix}-{number:04d}"
            high_water = int(connection.execute("SELECT COALESCE(MAX(id),0) FROM activity WHERE run_id=?", (self.run_id,)).fetchone()[0])
            connection.execute("""INSERT INTO agents
                (id,run_id,name,provider,model,effort,process_state,created_at,last_activity_id,last_wake_scan_id)
                VALUES (?,?,?,?,?,?,'created',?,?,?)""",
                (agent_id, self.run_id, name, template["provider"], template["model"], template["effort"], _now(), high_water, high_water))
            connection.execute("""INSERT INTO population_births
                (birth_id,run_id,agent_id,template_id,call_id,kind,state,prepaid,created_at)
                VALUES (?,?,?,?,?,?,'reserved',1,?)""",
                (birth_id, self.run_id, agent_id, template["id"], call["id"] if call else None, "initial" if initial else "call", current))
            connection.execute("""UPDATE population_state SET bucket_tokens=?,total_births=total_births+1,
                initial_reserved=initial_reserved+?,template_cursor=template_cursor+1 WHERE run_id=?""",
                (tokens - 1, int(initial), self.run_id))
            if call:
                self._record_admission(connection, call, "birth", birth_id, current)
                connection.execute("UPDATE participation_calls SET state='filled',fulfilled_by=?,fulfillment_kind='new_peer',birth_id=? WHERE id=?",
                                   (agent_id, birth_id, call["id"]))
                connection.execute("INSERT OR IGNORE INTO thread_subscriptions VALUES (?,?,0,?)", (agent_id, call["thread_id"], _now()))
            return self._birth(connection, birth_id)

    def _birth(self, connection: sqlite3.Connection, birth_id: str) -> dict[str, Any]:
        row = connection.execute("""SELECT b.*,c.reason,c.thread_id FROM population_births b
            LEFT JOIN participation_calls c ON c.id=b.call_id WHERE b.birth_id=? AND b.run_id=?""", (birth_id, self.run_id)).fetchone()
        if row is None:
            raise KeyError("birth does not belong to this run")
        value = dict(row)
        value["agent"] = dict(self._agent(connection, str(row["agent_id"]), allow_retired=True))
        return value

    def birth_for_agent(self, agent_id: str) -> dict[str, Any] | None:
        with self.forum._connection() as connection:
            row = connection.execute("SELECT birth_id FROM population_births WHERE agent_id=? AND run_id=?", (agent_id, self.run_id)).fetchone()
            return self._birth(connection, str(row[0])) if row else None

    def pending_births(self) -> list[dict[str, Any]]:
        with self.forum._connection() as connection:
            rows = connection.execute("""SELECT b.birth_id FROM population_births b JOIN agents a ON a.id=b.agent_id
                WHERE b.run_id=? AND b.state IN ('reserved','ready') AND b.invocation_started=0
                AND a.process_state!='retired' ORDER BY b.created_at,b.birth_id""", (self.run_id,)).fetchall()
            return [self._birth(connection, str(row[0])) for row in rows]

    def finish_birth(self, birth_id: str, *, succeeded: bool, error: str | None = None) -> None:
        with self.forum._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            birth = self._birth(connection, birth_id)
            if birth["state"] not in {"reserved", "ready"} or birth["invocation_started"]:
                return
            state = "ready" if succeeded else "failed"
            connection.execute("UPDATE population_births SET state=?,finished_at=?,error=? WHERE birth_id=?",
                               (state, time.time(), None if succeeded else str(error or "workspace preparation failed")[:2000], birth_id))
            if not succeeded:
                if birth["call_id"]:
                    connection.execute("UPDATE participation_calls SET state='failed' WHERE id=? AND birth_id=?",
                                       (birth["call_id"], birth_id))
                connection.execute("""UPDATE agents SET process_state='retired',retired_at=COALESCE(retired_at,?),
                    retire_reason='Initial preparation failed',pid=NULL WHERE id=? AND process_state NOT IN ('running','retired')""", (_now(), birth["agent_id"]))

    def admit_invocation(self, connection: sqlite3.Connection, agent_id: str, *, new_session: bool) -> bool:
        """Called inside ExecutionStore.claim's transaction; no secondary writer."""
        if connection.execute("SELECT 1 FROM population_config WHERE run_id=?", (self.run_id,)).fetchone() is None:
            return True
        agent = self._agent(connection, agent_id, allow_retired=True)
        if agent["process_state"] == "retired":
            return False
        policy, counters, tokens = self._refill(connection, time.time())
        if agent["participation_state"] == "parked" and self._resident_count(connection) >= policy.max_agents:
            return False
        if int(counters["invocations_started"]) >= policy.max_invocations:
            return False
        birth = connection.execute("SELECT * FROM population_births WHERE agent_id=? AND run_id=?", (agent_id, self.run_id)).fetchone()
        prepaid = bool(birth and birth["prepaid"] and not birth["invocation_started"])
        charge_birth = bool(new_session and not prepaid)
        if charge_birth and (tokens < 1 or int(counters["total_births"]) >= policy.max_births):
            return False
        connection.execute("""UPDATE population_state SET invocations_started=invocations_started+1,
            total_births=total_births+?,bucket_tokens=? WHERE run_id=?""", (int(charge_birth), tokens - int(charge_birth), self.run_id))
        if birth and not birth["invocation_started"]:
            connection.execute("UPDATE population_births SET invocation_started=1,state='started' WHERE birth_id=?", (birth["birth_id"],))
        if agent["participation_state"] == "parked":
            connection.execute("UPDATE agents SET participation_state='resident',parked_at=NULL WHERE id=?", (agent_id,))
        return True

    def park_idle_peers(self, *, now: float | None = None,
                        available_agent_ids: Iterable[str] | None = None) -> list[str]:
        current = time.time() if now is None else now
        if isinstance(current, bool) or not isinstance(current, (int, float)) or not math.isfinite(current):
            raise ValueError("now must be finite")
        available = None if available_agent_ids is None else set(available_agent_ids)
        with self.forum._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if connection.execute("SELECT 1 FROM population_config WHERE run_id=?", (self.run_id,)).fetchone() is None:
                return []
            policy = self._policy(connection, self.run_id)
            self._expire(connection, current)
            clauses = ["a.run_id=?", "a.participation_state!='parked'",
                       "a.process_state IN ('dormant','failed','blocked','exited')",
                       "(julianday(COALESCE(a.exited_at,a.created_at))-2440587.5)*86400<=?",
                       "NOT EXISTS (SELECT 1 FROM participation_offers o WHERE o.agent_id=a.id AND o.state='pending')"]
            if self._execution_available(connection):
                clauses.append("NOT EXISTS (SELECT 1 FROM execution_requests r WHERE r.agent_id=a.id AND r.state IN ('queued','running'))")
            rows = connection.execute(f"SELECT a.id FROM agents a WHERE {' AND '.join(clauses)}",
                                      (self.run_id, current - policy.idle_timeout)).fetchall()
            parked = [str(row["id"]) for row in rows if available is None or str(row["id"]) in available]
            connection.executemany("UPDATE agents SET participation_state='parked',parked_at=? WHERE id=?", ((_now(), item) for item in parked))
            return parked

    def summary(self) -> dict[str, Any]:
        if not self.configured():
            return {"configured": False, "enabled": False, "policy": None, "exhausted": False}
        now = time.time()
        with self.forum._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            policy, counters, tokens = self._refill(connection, now)
            self._expire(connection, now)
            enabled = bool(connection.execute("SELECT enabled FROM population_config WHERE run_id=?", (self.run_id,)).fetchone()[0])
            live = self._resident_count(connection)
            members = connection.execute("""SELECT COUNT(*) AS total,
                COALESCE(SUM(process_state!='retired' AND participation_state='parked'),0) AS parked,
                COALESCE(SUM(process_state='running'),0) AS running,
                COALESCE(SUM(process_state='dormant'),0) AS dormant,
                COALESCE(SUM(process_state='retired'),0) AS retired FROM agents WHERE run_id=?""", (self.run_id,)).fetchone()
            running = int(members["running"])
            if self._execution_available(connection) and connection.execute(
                    "SELECT 1 FROM execution_requests WHERE run_id=? LIMIT 1", (self.run_id,)).fetchone():
                running = int(connection.execute("SELECT COUNT(*) FROM execution_requests WHERE run_id=? AND state='running'",
                                                 (self.run_id,)).fetchone()[0])
            offers = int(connection.execute("SELECT COUNT(*) FROM participation_offers WHERE run_id=? AND state='pending'", (self.run_id,)).fetchone()[0])
            pending = int(connection.execute("SELECT COUNT(*) FROM population_births b JOIN agents a ON a.id=b.agent_id WHERE b.run_id=? AND b.state IN ('reserved','ready') AND b.invocation_started=0 AND a.process_state!='retired'", (self.run_id,)).fetchone()[0])
            calls = int(connection.execute("SELECT COUNT(*) FROM participation_calls WHERE run_id=? AND state='open'", (self.run_id,)).fetchone()[0])
            ready = int(connection.execute("""SELECT COUNT(*) FROM participation_calls c
                LEFT JOIN agent_templates t ON t.id=c.template_id
                WHERE c.run_id=? AND c.state='open' AND c.created_at<=?
                  AND (c.template_id IS NULL OR t.enabled=1)""", (self.run_id, now - policy.participation_grace)).fetchone()[0])
        births_exhausted = int(counters["total_births"]) >= policy.max_births
        invocations_exhausted = int(counters["invocations_started"]) >= policy.max_invocations
        return {
            "configured": True, "enabled": enabled, "policy": asdict(policy),
            "initial_reserved": int(counters["initial_reserved"]),
            "initial_remaining": max(0, policy.initial_agents - int(counters["initial_reserved"])) if enabled else 0,
            "live_agents": live, "resident_agents": live,
            "parked_agents": int(members["parked"]), "running_agents": running,
            "dormant_agents": int(members["dormant"]), "retired_agents": int(members["retired"]),
            "total_agents": int(members["total"]), "pending_offers": offers,
            "pending_births": pending, "open_calls": calls, "ready_calls": ready,
            "total_births": int(counters["total_births"]), "invocations_started": int(counters["invocations_started"]),
            "birth_tokens": tokens, "next_birth_in_seconds": max(0.0, (1 - tokens) * 60 / policy.births_per_minute),
            "births_exhausted": births_exhausted, "invocations_exhausted": invocations_exhausted,
            "exhausted": invocations_exhausted, "adopted_agents": int(counters["adopted_agents"]),
            "adopted_known_starts": int(counters["adopted_known_starts"]),
            "migration_note": "Existing invocation records are counted where available; historical fresh-session counts are a known minimum." if counters["adopted_agents"] else None,
        }
