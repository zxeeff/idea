from __future__ import annotations

import sqlite3
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .forum import Forum


MAX_DEFINITION_BYTES = 16 * 1024
MAX_FOCUS_BYTES = 2 * 1024
PREVIEW_BYTES = 512


def initialize(connection: sqlite3.Connection) -> None:
    """Add immutable approach definitions and independent, voluntary membership."""
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS approaches (
            id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL REFERENCES runs(id),
            thread_id TEXT NOT NULL UNIQUE REFERENCES threads(id),
            author TEXT NOT NULL,
            hypothesis TEXT NOT NULL,
            next_check TEXT NOT NULL,
            parent_id TEXT REFERENCES approaches(id),
            event_id INTEGER NOT NULL REFERENCES activity(id),
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS approaches_by_run ON approaches(run_id, id);
        CREATE INDEX IF NOT EXISTS approaches_by_parent ON approaches(parent_id, id);
        CREATE TABLE IF NOT EXISTS approach_members (
            approach_id TEXT NOT NULL REFERENCES approaches(id),
            agent_id TEXT NOT NULL REFERENCES agents(id),
            focus TEXT NOT NULL DEFAULT '',
            wake INTEGER NOT NULL DEFAULT 0 CHECK (wake IN (0, 1)),
            joined_at TEXT NOT NULL,
            left_at TEXT,
            PRIMARY KEY (approach_id, agent_id)
        );
        CREATE INDEX IF NOT EXISTS approach_members_by_agent
            ON approach_members(agent_id, left_at, approach_id);
        CREATE INDEX IF NOT EXISTS approach_members_by_approach
            ON approach_members(approach_id, left_at, agent_id);
        """
    )


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


def _text(value: str, name: str, maximum: int, *, required: bool = True) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be text")
    if len(value.encode("utf-8")) > maximum:
        raise ValueError(f"{name} must be at most {maximum} UTF-8 bytes")
    value = value.strip()
    if required and not value:
        raise ValueError(f"{name} is required")
    return value


def _preview(value: str, maximum: int = PREVIEW_BYTES) -> tuple[str, bool]:
    encoded = value.encode("utf-8")
    return encoded[:maximum].decode("utf-8", errors="ignore"), len(encoded) > maximum


def _identifier(value: str, name: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.encode("utf-8")) > 256:
        raise ValueError(f"{name} must be a nonempty record ID string of at most 256 UTF-8 bytes")
    return value


class ApproachStore:
    """Describe experiments without assigning peers, launching work, or judging results."""

    def __init__(self, forum: Forum, run_id: str):
        self.forum = forum
        self.run_id = _identifier(run_id, "run_id")
        forum.get_run(run_id)
        with forum._connection() as connection:
            if connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'approaches'"
            ).fetchone() is None:
                initialize(connection)

    def _approach(self, connection: sqlite3.Connection, approach_id: str) -> sqlite3.Row:
        _identifier(approach_id, "approach_id")
        row = connection.execute("SELECT * FROM approaches WHERE id = ?", (approach_id,)).fetchone()
        if row is None:
            raise KeyError(f"unknown approach: {approach_id}")
        if row["run_id"] != self.run_id:
            raise ValueError("approach belongs to a different run")
        return row

    def _agent(self, connection: sqlite3.Connection, agent_id: str) -> sqlite3.Row:
        _identifier(agent_id, "agent_id")
        row = connection.execute("SELECT * FROM agents WHERE id = ?", (agent_id,)).fetchone()
        if row is None:
            raise KeyError(f"unknown agent: {agent_id}")
        if row["run_id"] != self.run_id:
            raise ValueError("agent belongs to a different run")
        return row

    def _thread(self, connection: sqlite3.Connection, thread_id: str) -> sqlite3.Row:
        _identifier(thread_id, "thread_id")
        row = connection.execute("SELECT id, run_id FROM threads WHERE id = ?", (thread_id,)).fetchone()
        if row is None:
            raise KeyError(f"unknown thread: {thread_id}")
        if row["run_id"] != self.run_id:
            raise ValueError("thread belongs to a different run")
        return row

    def create(
        self, thread_id: str, author: str, *, hypothesis: str, next_check: str,
        parent_id: str | None = None,
    ) -> dict[str, Any]:
        author = _text(author, "author", 1024)
        hypothesis = _text(hypothesis, "hypothesis", MAX_DEFINITION_BYTES)
        next_check = _text(next_check, "next_check", MAX_DEFINITION_BYTES)
        if parent_id is not None:
            parent_id = _text(parent_id, "parent_id", 256)
        with self.forum._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._thread(connection, thread_id)
            if parent_id is not None:
                self._approach(connection, parent_id)
            existing = connection.execute(
                "SELECT * FROM approaches WHERE thread_id = ?", (thread_id,)
            ).fetchone()
            if existing is not None:
                if (existing["author"], existing["hypothesis"], existing["next_check"], existing["parent_id"]) != (
                    author, hypothesis, next_check, parent_id
                ):
                    raise ValueError("an approach is immutable; use a new thread and parent_id for another hypothesis")
                return self._enrich(connection, [dict(existing)])[0]
            approach_id = f"approach_{uuid.uuid4().hex[:12]}"
            body = f"Approach {approach_id}\n\nHypothesis:\n{hypothesis}\n\nNext check:\n{next_check}"
            if parent_id is not None:
                body += f"\n\nDerived from: {parent_id}"
            comment = self.forum._add_comment(
                connection, thread_id, author, body, notification_text=""
            )
            connection.execute(
                """INSERT INTO approaches(
                    id, run_id, thread_id, author, hypothesis, next_check, parent_id, event_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (approach_id, self.run_id, thread_id, author, hypothesis, next_check,
                 parent_id, comment["event_id"], _now()),
            )
            return self._enrich(connection, [dict(self._approach(connection, approach_id))])[0]

    def _enrich(
        self, connection: sqlite3.Connection, items: list[dict[str, Any]], *,
        preview: bool = False, agent_id: str | None = None,
    ) -> list[dict[str, Any]]:
        if not items:
            return items
        marks = ",".join("?" for _ in items)
        ids = [item["id"] for item in items]
        counts = {row["approach_id"]: row for row in connection.execute(
            f"""SELECT m.approach_id, COUNT(*) AS member_count,
                SUM(a.process_state = 'running') AS running_members,
                SUM(a.participation_state = 'parked') AS parked_members
                FROM approach_members m JOIN agents a ON a.id = m.agent_id
                WHERE m.approach_id IN ({marks}) AND m.left_at IS NULL
                    AND a.run_id = ? AND a.process_state != 'retired'
                GROUP BY m.approach_id""", (*ids, self.run_id),
        )}
        titles = {row["id"]: row["title"] for row in connection.execute(
            f"SELECT id, SUBSTR(title, 1, 240) AS title FROM threads WHERE id IN ({marks})",
            [item["thread_id"] for item in items],
        )}
        memberships = {}
        if agent_id is not None:
            memberships = {row["approach_id"]: row for row in connection.execute(
                f"""SELECT * FROM approach_members WHERE approach_id IN ({marks})
                    AND agent_id = ? AND left_at IS NULL""", (*ids, agent_id),
            )}
        for item in items:
            count = counts.get(item["id"])
            for key in ("member_count", "running_members", "parked_members"):
                item[key] = int(count[key]) if count is not None else 0
            item["thread_title"] = _preview(titles.get(item["thread_id"], ""), 240)[0]
            if preview:
                for key in ("hypothesis", "next_check"):
                    item[key], item[f"{key}_truncated"] = _preview(item[key])
            if agent_id is not None:
                membership = memberships.get(item["id"])
                item["membership"] = self._membership(membership, item["id"], agent_id, item["thread_id"])
                item["joined"] = membership is not None
        return items

    def get(self, approach_id: str) -> dict[str, Any]:
        with self.forum._connection() as connection:
            connection.execute("BEGIN")
            return self._enrich(connection, [dict(self._approach(connection, approach_id))])[0]

    def list(
        self, *, query: str = "", limit: int = 30, after: str | None = None,
        agent_id: str | None = None, thread_id: str | None = None,
    ) -> dict[str, Any]:
        self.forum._page_limit(limit)
        cursor = self.forum._keyset_cursor(after)
        query = _text(query, "query", 2048, required=False)
        conditions = ["p.run_id = ?", "p.id > ?"]
        parameters: list[Any] = [self.run_id, cursor]
        if query:
            pattern = "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            conditions.append("""(p.hypothesis LIKE ? ESCAPE '\\' OR p.next_check LIKE ? ESCAPE '\\'
                OR t.title LIKE ? ESCAPE '\\')""")
            parameters.extend([pattern] * 3)
        if thread_id is not None:
            conditions.append("p.thread_id = ?")
            parameters.append(thread_id)
        if agent_id is not None:
            conditions.append("""EXISTS (SELECT 1 FROM approach_members m JOIN agents a ON a.id = m.agent_id
                WHERE m.approach_id = p.id AND m.agent_id = ? AND m.left_at IS NULL
                    AND a.process_state != 'retired')""")
            parameters.append(agent_id)
        with self.forum._connection() as connection:
            connection.execute("BEGIN")
            if agent_id is not None:
                self._agent(connection, agent_id)
            if thread_id is not None:
                self._thread(connection, thread_id)
            rows = connection.execute(
                f"SELECT p.* FROM approaches p JOIN threads t ON t.id = p.thread_id WHERE {' AND '.join(conditions)} ORDER BY p.id LIMIT ?",
                (*parameters, limit + 1),
            ).fetchall()
            items = self._enrich(connection, [dict(row) for row in rows[:limit]], preview=True, agent_id=agent_id)
            return {"items": items, "next_cursor": items[-1]["id"] if len(rows) > limit else None}

    @staticmethod
    def _membership(
        row: sqlite3.Row | None, approach_id: str, agent_id: str, thread_id: str,
    ) -> dict[str, Any]:
        item = dict(row) if row is not None else {
            "approach_id": approach_id, "agent_id": agent_id, "focus": "", "wake": False,
            "joined_at": None, "left_at": None,
        }
        item["wake"] = bool(item["wake"])
        item["joined"] = row is not None and row["left_at"] is None
        item["thread_id"] = thread_id
        return item

    def join(
        self, approach_id: str, agent_id: str, *, focus: str = "", wake: bool = False,
    ) -> dict[str, Any]:
        focus = _text(focus, "focus", MAX_FOCUS_BYTES, required=False)
        if not isinstance(wake, bool):
            raise ValueError("wake must be a boolean")
        with self.forum._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            approach = self._approach(connection, approach_id)
            agent = self._agent(connection, agent_id)
            if agent["process_state"] == "retired":
                raise ValueError("retired agents cannot join an approach")
            connection.execute(
                """INSERT INTO approach_members(approach_id, agent_id, focus, wake, joined_at, left_at)
                VALUES (?, ?, ?, ?, ?, NULL)
                ON CONFLICT(approach_id, agent_id) DO UPDATE SET focus = excluded.focus,
                    wake = excluded.wake,
                    joined_at = CASE WHEN approach_members.left_at IS NULL
                        THEN approach_members.joined_at ELSE excluded.joined_at END,
                    left_at = NULL""",
                (approach_id, agent_id, focus, int(wake), _now()),
            )
            row = connection.execute(
                "SELECT * FROM approach_members WHERE approach_id = ? AND agent_id = ?", (approach_id, agent_id)
            ).fetchone()
            return self._membership(row, approach_id, agent_id, approach["thread_id"])

    def leave(self, approach_id: str, agent_id: str) -> dict[str, Any]:
        with self.forum._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            approach = self._approach(connection, approach_id)
            self._agent(connection, agent_id)
            connection.execute(
                "UPDATE approach_members SET left_at = ? WHERE approach_id = ? AND agent_id = ? AND left_at IS NULL",
                (_now(), approach_id, agent_id),
            )
            row = connection.execute(
                "SELECT * FROM approach_members WHERE approach_id = ? AND agent_id = ?", (approach_id, agent_id)
            ).fetchone()
            return self._membership(row, approach_id, agent_id, approach["thread_id"])

    def members(self, approach_id: str, *, limit: int = 30, after: str | None = None) -> dict[str, Any]:
        self.forum._page_limit(limit)
        cursor = self.forum._keyset_cursor(after)
        with self.forum._connection() as connection:
            connection.execute("BEGIN")
            approach = self._approach(connection, approach_id)
            rows = connection.execute(
                """SELECT m.*, SUBSTR(a.name, 1, 1025) AS name, a.provider,
                    SUBSTR(a.model, 1, 1025) AS model, a.effort, a.process_state, a.participation_state
                    FROM approach_members m JOIN agents a ON a.id = m.agent_id
                    WHERE m.approach_id = ? AND m.agent_id > ? AND m.left_at IS NULL
                        AND a.run_id = ? AND a.process_state != 'retired'
                    ORDER BY m.agent_id LIMIT ?""",
                (approach_id, cursor, self.run_id, limit + 1),
            ).fetchall()
            items = [self._membership(row, approach_id, row["agent_id"], approach["thread_id"]) for row in rows[:limit]]
            for item in items:
                for key in ("name", "model"):
                    item[key], item[f"{key}_truncated"] = _preview(item[key], 1024)
            return {"items": items, "next_cursor": items[-1]["agent_id"] if len(rows) > limit else None}
