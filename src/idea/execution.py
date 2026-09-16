"""Single-host execution admission and durable delivery attempts.

This module accounts for processes, not the meaning of peers' work. The run
lock is held by the launcher and its trusted process supervisors, so a new
launcher cannot reclaim slots while the previous process groups are running.
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .forum import Forum, _now


@dataclass(frozen=True, slots=True)
class ExecutionPolicy:
    max_concurrent: int = 16
    max_codex: int | None = None
    max_claude: int | None = None

    def __post_init__(self) -> None:
        for name in ("max_concurrent", "max_codex", "max_claude"):
            value = getattr(self, name)
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 500
            ):
                raise ValueError(f"{name} must be between 1 and 500")

    def provider_limit(self, provider: str) -> int:
        value = self.max_codex if provider == "openai" else self.max_claude
        return min(value or self.max_concurrent, self.max_concurrent)


class RunLock:
    """An OS-owned lock; stale files are harmless and are never PID leases."""

    def __init__(self, state_dir: Path, run_id: str):
        self.path = state_dir / "runs" / run_id / "execution.lock"
        self.fd: int | None = None

    def __enter__(self) -> RunLock:
        import fcntl

        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            raise RuntimeError(
                "this run already has a launcher or live provider process; "
                "stop it before resuming the same run"
            ) from None
        self.fd = fd
        return self

    def __exit__(self, *args: object) -> None:
        if self.fd is not None:
            # Never LOCK_UN: that would release the shared lock even if a child
            # inherited the same open-file description and is still running.
            os.close(self.fd)
            self.fd = None


class ExecutionStore:
    def __init__(self, forum: Forum, run_id: str, *, population=None):
        self.forum = forum
        self.run_id = run_id
        self.population = population
        forum.get_run(run_id)
        with forum._connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS execution_policies (
                    run_id TEXT PRIMARY KEY REFERENCES runs(id),
                    max_concurrent INTEGER NOT NULL,
                    max_codex INTEGER,
                    max_claude INTEGER
                );
                CREATE TABLE IF NOT EXISTS execution_requests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL REFERENCES runs(id),
                    agent_id TEXT NOT NULL REFERENCES agents(id),
                    kind TEXT NOT NULL,
                    state TEXT NOT NULL,
                    event_ids_json TEXT NOT NULL DEFAULT '[]',
                    failure_watermark INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    finished_at TEXT,
                    exit_code INTEGER
                );
                CREATE UNIQUE INDEX IF NOT EXISTS one_ready_execution_per_agent
                    ON execution_requests(agent_id)
                    WHERE state IN ('queued', 'running');
                CREATE INDEX IF NOT EXISTS ready_execution_by_run
                    ON execution_requests(run_id, state, id);
                CREATE INDEX IF NOT EXISTS execution_history_by_agent
                    ON execution_requests(agent_id, id DESC);
                CREATE TABLE IF NOT EXISTS execution_attempts (
                    id TEXT PRIMARY KEY,
                    request_id INTEGER NOT NULL REFERENCES execution_requests(id),
                    state TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT,
                    exit_code INTEGER
                );
                """
            )

    def configure(
        self,
        *,
        max_concurrent: int | None = None,
        max_codex: int | None = None,
        max_claude: int | None = None,
    ) -> ExecutionPolicy:
        with self.forum._connection() as connection:
            previous = connection.execute(
                "SELECT * FROM execution_policies WHERE run_id = ?", (self.run_id,)
            ).fetchone()
            policy = ExecutionPolicy(
                max_concurrent=(
                    max_concurrent if max_concurrent is not None
                    else (int(previous["max_concurrent"]) if previous else 16)
                ),
                max_codex=max_codex if max_codex is not None else (
                    previous["max_codex"] if previous else None
                ),
                max_claude=max_claude if max_claude is not None else (
                    previous["max_claude"] if previous else None
                ),
            )
            connection.execute(
                """INSERT INTO execution_policies VALUES (?, ?, ?, ?)
                   ON CONFLICT(run_id) DO UPDATE SET
                   max_concurrent=excluded.max_concurrent,
                   max_codex=excluded.max_codex, max_claude=excluded.max_claude""",
                (self.run_id, policy.max_concurrent, policy.max_codex, policy.max_claude),
            )
        return policy

    def recover(self) -> None:
        """Call only after acquiring RunLock (old provider holders are gone)."""
        with self.forum._connection() as connection:
            connection.execute(
                """UPDATE execution_attempts SET state='interrupted', finished_at=?
                   WHERE state='running' AND request_id IN
                   (SELECT id FROM execution_requests WHERE run_id=?)""",
                (_now(), self.run_id),
            )
            connection.execute(
                "UPDATE execution_requests SET state='queued' WHERE run_id=? AND state='running'",
                (self.run_id,),
            )

    def enqueue(self, agent_id: str, *, kind: str = "notification") -> int:
        with self.forum._connection() as connection:
            connection.execute(
                """INSERT OR IGNORE INTO execution_requests
                   (run_id,agent_id,kind,state,created_at) VALUES (?, ?, ?, 'queued', ?)""",
                (self.run_id, agent_id, kind, _now()),
            )
            row = connection.execute(
                """SELECT id FROM execution_requests
                   WHERE agent_id=? AND state IN ('queued','running')""", (agent_id,)
            ).fetchone()
        assert row is not None
        return int(row["id"])

    def held_through(self, agent_id: str) -> int:
        with self.forum._connection() as connection:
            row = connection.execute(
                """SELECT state,failure_watermark FROM execution_requests
                   WHERE agent_id=? ORDER BY id DESC LIMIT 1""",
                (agent_id,),
            ).fetchone()
        return int(row["failure_watermark"]) if row and row["state"] == "failed" else 0

    def queued(self) -> list[dict[str, Any]]:
        with self.forum._connection() as connection:
            rows = connection.execute(
                """SELECT r.*, a.provider FROM execution_requests r
                   JOIN agents a ON a.id=r.agent_id
                   WHERE r.run_id=? AND r.state='queued'
                   ORDER BY CASE WHEN julianday('now')-julianday(r.created_at) >= 1.0/1440
                     THEN 0 ELSE 1 + COALESCE((SELECT MIN(d.priority)
                         FROM notification_deliveries d WHERE d.agent_id=r.agent_id
                         AND d.acknowledged_at IS NULL AND d.withdrawn_at IS NULL), 3) END, r.id""",
                (self.run_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def claim(self, request_id: int, event_ids: list[int], policy: ExecutionPolicy) -> str | None:
        with self.forum._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            request = connection.execute(
                """SELECT r.state,r.agent_id,a.provider,a.process_state,a.session_id FROM execution_requests r
                   JOIN agents a ON a.id=r.agent_id WHERE r.id=? AND r.run_id=?""",
                (request_id, self.run_id),
            ).fetchone()
            if request is None or request["state"] != "queued" or request["process_state"] == "retired":
                return None
            if event_ids:
                # Context was assembled before taking this write lock. An offer
                # can be withdrawn or acknowledged in that interval; rebuild
                # context on the next pass before charging any invocation.
                valid = connection.execute("""SELECT COUNT(*) FROM notification_deliveries
                    WHERE agent_id=? AND run_id=? AND acknowledged_at IS NULL AND withdrawn_at IS NULL
                    AND event_id IN (SELECT value FROM json_each(?))""",
                    (request["agent_id"], self.run_id, json.dumps(event_ids))).fetchone()[0]
                if valid != len(set(event_ids)):
                    return None
            rows = connection.execute(
                """SELECT a.provider,COUNT(*) AS n FROM execution_requests r
                   JOIN agents a ON a.id=r.agent_id
                   WHERE r.run_id=? AND r.state='running' GROUP BY a.provider""",
                (self.run_id,),
            ).fetchall()
            counts = {str(row["provider"]): int(row["n"]) for row in rows}
            if sum(counts.values()) >= policy.max_concurrent:
                return None
            if counts.get(str(request["provider"]), 0) >= policy.provider_limit(str(request["provider"])):
                return None
            if self.population is not None and not self.population.admit_invocation(
                connection, str(request["agent_id"]),
                new_session=not request["session_id"] or request["process_state"] == "blocked",
            ):
                return None
            attempt_id = uuid.uuid4().hex
            connection.execute(
                """UPDATE execution_requests SET state='running',event_ids_json=?,
                   failure_watermark=(SELECT COALESCE(MAX(id),0) FROM activity WHERE run_id=?)
                   WHERE id=?""",
                (json.dumps(event_ids), self.run_id, request_id),
            )
            connection.execute(
                "INSERT INTO execution_attempts VALUES (?, ?, 'running', ?, NULL, NULL)",
                (attempt_id, request_id, _now()),
            )
        return attempt_id

    def finish(
        self,
        request_id: int,
        attempt_id: str,
        *,
        exit_code: int,
        delivery_succeeded: bool = True,
        failure_watermark: int | None = None,
    ) -> None:
        with self.forum._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """SELECT r.* FROM execution_requests r JOIN execution_attempts t
                   ON t.request_id=r.id WHERE r.id=? AND t.id=? AND t.state='running'""",
                (request_id, attempt_id),
            ).fetchone()
            if row is None:
                return
            # A provider can report an unsuccessful turn while its CLI exits
            # normally. Keep that status separate from the raw process code.
            success = exit_code == 0 and delivery_succeeded
            state = "complete" if success else "failed"
            now = _now()
            watermark = int(row["failure_watermark"]) if failure_watermark is None else failure_watermark
            if success:
                self.forum.acknowledge_notifications(
                    str(row["agent_id"]), json.loads(row["event_ids_json"]),
                    connection=connection,
                )
            connection.execute(
                """UPDATE execution_requests SET state=?,finished_at=?,exit_code=?,
                   failure_watermark=? WHERE id=?""",
                (state, now, exit_code, 0 if success else watermark, request_id),
            )
            connection.execute(
                "UPDATE execution_attempts SET state=?,finished_at=?,exit_code=? WHERE id=?",
                (state, now, exit_code, attempt_id),
            )

    def cancel_queued(self, agent_id: str) -> None:
        with self.forum._connection() as connection:
            connection.execute(
                """UPDATE execution_requests SET state='cancelled',finished_at=?
                   WHERE agent_id=? AND state='queued'""", (_now(), agent_id)
            )

    def cancel_claim(self, request_id: int, attempt_id: str) -> None:
        """A peer retired before its provider started; consume no notification."""
        with self.forum._connection() as connection:
            connection.execute(
                "UPDATE execution_requests SET state='cancelled',finished_at=? WHERE id=? AND state='running'",
                (_now(), request_id),
            )
            connection.execute(
                "UPDATE execution_attempts SET state='cancelled',finished_at=? WHERE id=? AND state='running'",
                (_now(), attempt_id),
            )
