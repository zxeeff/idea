from __future__ import annotations

import hashlib
import json
import re
import shutil
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable

from .domain import AgentProfile, ProcessState
from . import provenance


_MENTION_RE = re.compile(r"(?<![\w-])@([A-Za-z0-9][A-Za-z0-9_-]*)(?![\w-])")


def _discussion_comment_sql(alias: str) -> str:
    """SQL predicate for comments intended for the human discussion view."""

    if alias not in {"c", "search_comments"}:
        raise ValueError("unsupported comment alias")
    return (
        f"{alias}.author != 'system' AND NOT EXISTS ("
        "SELECT 1 FROM comment_presentation presentation "
        f"WHERE presentation.comment_id = {alias}.id "
        "AND presentation.mode = 'coordination')"
    )


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


class Forum:
    """A deliberately small, append-only forum shared by every agent."""

    def __init__(self, state_dir: str | Path):
        self.state_dir = Path(state_dir).expanduser().resolve()
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.state_dir / "forum.sqlite3"
        self.attachments_dir = self.state_dir / "attachments"
        self.attachments_dir.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    @contextmanager
    def _connection(self):
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connection() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY,
                    goal TEXT NOT NULL,
                    workspace TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS agents (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES runs(id),
                    name TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    model TEXT NOT NULL,
                    effort TEXT NOT NULL,
                    process_state TEXT NOT NULL,
                    pid INTEGER,
                    exit_code INTEGER,
                    session_id TEXT,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    exited_at TEXT,
                    last_activity_id INTEGER NOT NULL DEFAULT 0,
                    last_wake_scan_id INTEGER NOT NULL DEFAULT 0,
                    retired_at TEXT,
                    retire_reason TEXT,
                    UNIQUE(run_id, name)
                );

                CREATE TABLE IF NOT EXISTS threads (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES runs(id),
                    author TEXT NOT NULL,
                    title TEXT NOT NULL,
                    body TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS comments (
                    id TEXT PRIMARY KEY,
                    thread_id TEXT NOT NULL REFERENCES threads(id),
                    author TEXT NOT NULL,
                    body TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS comment_presentation (
                    comment_id TEXT PRIMARY KEY REFERENCES comments(id),
                    mode TEXT NOT NULL CHECK(mode IN ('coordination')),
                    kind TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS attachments (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES runs(id),
                    thread_id TEXT REFERENCES threads(id),
                    author TEXT NOT NULL,
                    original_name TEXT NOT NULL,
                    description TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    size INTEGER NOT NULL,
                    stored_path TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS activity (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL REFERENCES runs(id),
                    author TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    subject_id TEXT NOT NULL,
                    thread_id TEXT,
                    audience_json TEXT,
                    notification_mode TEXT NOT NULL DEFAULT 'passive',
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS threads_by_run
                    ON threads(run_id, created_at);
                CREATE INDEX IF NOT EXISTS comments_by_thread
                    ON comments(thread_id, created_at);
                CREATE INDEX IF NOT EXISTS attachments_by_run
                    ON attachments(run_id, created_at);
                CREATE INDEX IF NOT EXISTS activity_by_run
                    ON activity(run_id, id);
                CREATE INDEX IF NOT EXISTS activity_by_thread
                    ON activity(thread_id, id);
                CREATE INDEX IF NOT EXISTS agents_by_run_name_nocase
                    ON agents(run_id, name COLLATE NOCASE);
                CREATE INDEX IF NOT EXISTS agents_by_run_id
                    ON agents(run_id, id);
                CREATE INDEX IF NOT EXISTS comments_by_thread_page
                    ON comments(thread_id, created_at, id);
                CREATE TABLE IF NOT EXISTS forum_migrations (
                    name TEXT PRIMARY KEY
                );
                CREATE TABLE IF NOT EXISTS thread_subscriptions (
                    agent_id TEXT NOT NULL REFERENCES agents(id),
                    thread_id TEXT NOT NULL REFERENCES threads(id),
                    wake INTEGER NOT NULL DEFAULT 0 CHECK (wake IN (0, 1)),
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(agent_id, thread_id)
                );
                CREATE INDEX IF NOT EXISTS subscriptions_by_thread
                    ON thread_subscriptions(thread_id, wake, agent_id);
                CREATE TABLE IF NOT EXISTS notification_deliveries (
                    agent_id TEXT NOT NULL REFERENCES agents(id),
                    event_id INTEGER NOT NULL REFERENCES activity(id),
                    run_id TEXT NOT NULL REFERENCES runs(id),
                    notification_reason TEXT NOT NULL,
                    priority INTEGER NOT NULL,
                    acknowledged_at TEXT,
                    withdrawn_at TEXT,
                    PRIMARY KEY(agent_id, event_id)
                );
                CREATE INDEX IF NOT EXISTS pending_notifications_by_agent
                    ON notification_deliveries(agent_id, priority, event_id)
                    WHERE acknowledged_at IS NULL;
                CREATE INDEX IF NOT EXISTS pending_notifications_by_run
                    ON notification_deliveries(run_id, agent_id, priority, event_id)
                    WHERE acknowledged_at IS NULL;
                """
            )
            provenance.initialize(connection)
            from .approaches import initialize as initialize_approaches
            from .reports import initialize as initialize_reports

            initialize_approaches(connection)
            initialize_reports(connection)
            columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(agents)").fetchall()
            }
            if "last_activity_id" not in columns:
                connection.execute(
                    "ALTER TABLE agents ADD COLUMN last_activity_id INTEGER NOT NULL DEFAULT 0"
                )
            if "last_wake_scan_id" not in columns:
                connection.execute(
                    "ALTER TABLE agents ADD COLUMN last_wake_scan_id INTEGER NOT NULL DEFAULT 0"
                )
                # Events created before notification modes existed already had a
                # chance to wake the old launcher. Do not replay them merely
                # because the schema was upgraded.
                connection.execute(
                    """
                    UPDATE agents
                    SET last_wake_scan_id = COALESCE(
                        (SELECT MAX(activity.id) FROM activity
                         WHERE activity.run_id = agents.run_id),
                        0
                    )
                    """
                )
            if "retired_at" not in columns:
                connection.execute("ALTER TABLE agents ADD COLUMN retired_at TEXT")
            if "retire_reason" not in columns:
                connection.execute("ALTER TABLE agents ADD COLUMN retire_reason TEXT")
            if "participation_state" not in columns:
                connection.execute("ALTER TABLE agents ADD COLUMN participation_state TEXT NOT NULL DEFAULT 'resident'")
            if "parked_at" not in columns:
                connection.execute("ALTER TABLE agents ADD COLUMN parked_at TEXT")
            delivery_columns = {row["name"] for row in connection.execute("PRAGMA table_info(notification_deliveries)")}
            if "withdrawn_at" not in delivery_columns:
                connection.execute("ALTER TABLE notification_deliveries ADD COLUMN withdrawn_at TEXT")
            connection.execute("""CREATE INDEX IF NOT EXISTS actionable_notifications_by_agent
                ON notification_deliveries(agent_id, priority, event_id)
                WHERE acknowledged_at IS NULL AND withdrawn_at IS NULL""")
            connection.execute("""CREATE INDEX IF NOT EXISTS actionable_notifications_by_run
                ON notification_deliveries(run_id, agent_id, priority, event_id)
                WHERE acknowledged_at IS NULL AND withdrawn_at IS NULL""")
            activity_columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(activity)").fetchall()
            }
            if "notification_mode" not in activity_columns:
                connection.execute(
                    "ALTER TABLE activity ADD COLUMN notification_mode TEXT "
                    "NOT NULL DEFAULT 'passive'"
                )
                # NULL audience used to mean every event was broadcast, so keep
                # that interpretation for rows which predate this migration.
                # The column default remains passive for compatibility with an
                # older launcher process that may still be winding down.
                connection.execute(
                    "UPDATE activity SET notification_mode = 'broadcast'"
                )
            self._migrate_comment_presentation(connection)
            self._migrate_notification_deliveries(connection)

    @staticmethod
    def _migrate_comment_presentation(connection: sqlite3.Connection) -> None:
        """Classify protocol comments written before presentation metadata existed."""

        connection.execute(
            """INSERT OR IGNORE INTO comment_presentation(comment_id,mode,kind)
               SELECT id,'coordination','system' FROM comments WHERE author='system'"""
        )
        tables = {
            str(row["name"])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        if not {"participation_calls", "call_participations"}.issubset(tables):
            return
        connection.execute(
            """INSERT OR IGNORE INTO comment_presentation(comment_id,mode,kind)
               SELECT comment.id,'coordination','participation_call'
               FROM comments comment
               JOIN participation_calls call ON call.thread_id=comment.thread_id
               JOIN agents requester ON requester.id=call.requester_agent_id
               WHERE comment.author=requester.name
                 AND comment.body='Open participation request ' || call.id || char(10) || char(10) || call.reason"""
        )
        connection.execute(
            """INSERT OR IGNORE INTO comment_presentation(comment_id,mode,kind)
               SELECT comment.id,'coordination','participation_status'
               FROM comments comment
               JOIN participation_calls call ON call.thread_id=comment.thread_id
               JOIN agents requester ON requester.id=call.requester_agent_id
               WHERE comment.author=requester.name
                 AND comment.body='Withdrew participation request ' || call.id || '.'"""
        )
        connection.execute(
            """INSERT OR IGNORE INTO comment_presentation(comment_id,mode,kind)
               SELECT comment.id,'coordination','participation_status'
               FROM comments comment
               JOIN participation_calls call ON call.thread_id=comment.thread_id
               JOIN call_participations participation ON participation.call_id=call.id
               JOIN agents volunteer ON volunteer.id=participation.agent_id
               WHERE comment.author=volunteer.name
                 AND comment.body='Volunteered to consider participation request ' || call.id || '.'"""
        )

    @staticmethod
    def _migrate_notification_deliveries(connection: sqlite3.Connection) -> None:
        # The marker and backfill commit together. INSERT acquires the writer
        # lock, so simultaneous client initialization cannot replay the migration.
        # The ordinary CLI read path must not acquire that writer lock again.
        if connection.execute(
            "SELECT 1 FROM forum_migrations WHERE name = 'attention-v1'"
        ).fetchone() is not None:
            return
        inserted = connection.execute(
            "INSERT OR IGNORE INTO forum_migrations(name) VALUES ('attention-v1')"
        )
        if not inserted.rowcount:
            return
        connection.execute(
            """
            INSERT OR IGNORE INTO notification_deliveries(
                agent_id, event_id, run_id, notification_reason, priority
            )
            SELECT p.id, a.id, a.run_id,
                   CASE a.notification_mode WHEN 'targeted' THEN 'mention'
                        ELSE 'broadcast' END,
                   CASE a.notification_mode WHEN 'targeted' THEN 0 ELSE 1 END
            FROM agents p JOIN activity a ON a.run_id = p.run_id
            WHERE p.process_state != 'retired' AND a.id > p.last_wake_scan_id
              AND a.author != p.name COLLATE NOCASE
              AND (
                a.notification_mode = 'broadcast' OR
                (a.notification_mode = 'targeted' AND EXISTS (
                    SELECT 1 FROM json_each(
                        CASE WHEN json_valid(a.audience_json) THEN a.audience_json ELSE '[]' END
                    ) recipients WHERE recipients.value = p.name
                ))
              )
            """
        )

    @staticmethod
    def _activity_notification(
        connection: sqlite3.Connection, run_id: str, text: str
    ) -> tuple[str, str | None]:
        tokens = {token.casefold() for token in _MENTION_RE.findall(text)}
        if "all" in tokens:
            return "broadcast", None
        if not tokens:
            return "passive", None
        mentioned = [str(row["name"]) for row in connection.execute(
            """
            SELECT name FROM agents
            WHERE run_id = ? AND name COLLATE NOCASE IN (SELECT value FROM json_each(?))
            ORDER BY created_at, id
            """,
            (run_id, json.dumps(sorted(tokens))),
        ).fetchall()]
        if mentioned:
            return "targeted", json.dumps(mentioned)
        return "passive", None

    def _record_activity(
        self,
        connection: sqlite3.Connection,
        *,
        run_id: str,
        author: str,
        kind: str,
        subject_id: str,
        thread_id: str | None,
        text: str,
    ) -> int:
        notification_mode, audience_json = self._activity_notification(
            connection, run_id, text
        )
        cursor = connection.execute(
            """
            INSERT INTO activity(
                run_id, author, kind, subject_id, thread_id, audience_json,
                notification_mode, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                author,
                kind,
                subject_id,
                thread_id,
                audience_json,
                notification_mode,
                _now(),
            ),
        )
        event_id = int(cursor.lastrowid)
        # Delivery is a recipient snapshot. Later joiners never inherit old
        # broadcasts, and a subscription can be changed without rewriting posts.
        if notification_mode == "broadcast":
            connection.execute(
                """
                INSERT INTO notification_deliveries(
                    agent_id, event_id, run_id, notification_reason, priority
                ) SELECT id, ?, run_id, 'broadcast', 1 FROM agents
                WHERE run_id = ? AND process_state != 'retired'
                  AND participation_state != 'parked'
                  AND name != ? COLLATE NOCASE
                """,
                (event_id, run_id, author),
            )
        elif notification_mode == "targeted":
            connection.execute(
                """
                INSERT INTO notification_deliveries(
                    agent_id, event_id, run_id, notification_reason, priority
                ) SELECT id, ?, run_id, 'mention', 0 FROM agents
                WHERE run_id = ? AND process_state != 'retired'
                  AND name != ? COLLATE NOCASE
                  AND name IN (SELECT value FROM json_each(?))
                """,
                (event_id, run_id, author, audience_json),
            )
        if thread_id:
            connection.execute(
                """
                INSERT OR IGNORE INTO notification_deliveries(
                    agent_id, event_id, run_id, notification_reason, priority
                ) SELECT p.id, ?, p.run_id, 'subscription', 2
                  FROM agents p JOIN (
                    SELECT s.agent_id FROM thread_subscriptions s WHERE s.thread_id=? AND s.wake=1
                    UNION
                    SELECT m.agent_id FROM approach_members m JOIN approaches a ON a.id=m.approach_id
                    WHERE a.thread_id=? AND m.left_at IS NULL AND m.wake=1
                  ) chosen ON chosen.agent_id=p.id
                WHERE p.run_id = ?
                  AND p.process_state != 'retired' AND p.name != ? COLLATE NOCASE
                """,
                (event_id, thread_id, thread_id, run_id, author),
            )
        return event_id

    def create_run(self, goal: str, workspace: str | Path) -> dict[str, Any]:
        run_id = _id("run")
        created_at = _now()
        workspace = str(Path(workspace).expanduser().resolve())
        with self._connection() as connection:
            connection.execute(
                "INSERT INTO runs(id, goal, workspace, created_at) VALUES (?, ?, ?, ?)",
                (run_id, goal, workspace, created_at),
            )
        return self.get_run(run_id)

    def get_run(self, run_id: str) -> dict[str, Any]:
        with self._connection() as connection:
            row = connection.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            raise KeyError(f"unknown run: {run_id}")
        return dict(row)

    def latest_run(self) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM runs ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
        return _dict(row)

    def list_runs(self) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute("SELECT * FROM runs ORDER BY created_at DESC").fetchall()
        return [dict(row) for row in rows]

    def register_agent(self, run_id: str, profile: AgentProfile) -> dict[str, Any]:
        agent_id = _id("agent")
        with self._connection() as connection:
            last_activity_id = connection.execute(
                "SELECT COALESCE(MAX(id), 0) FROM activity WHERE run_id = ?", (run_id,)
            ).fetchone()[0]
            connection.execute(
                """
                INSERT INTO agents(
                    id, run_id, name, provider, model, effort, process_state,
                    created_at, last_activity_id, last_wake_scan_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    agent_id,
                    run_id,
                    profile.name,
                    profile.provider.value,
                    profile.model,
                    profile.effort.value,
                    ProcessState.CREATED.value,
                    _now(),
                    last_activity_id,
                    last_activity_id,
                ),
            )
        return self.get_agent(agent_id)

    def get_agent(self, agent_id: str) -> dict[str, Any]:
        with self._connection() as connection:
            row = connection.execute("SELECT * FROM agents WHERE id = ?", (agent_id,)).fetchone()
        if row is None:
            raise KeyError(f"unknown agent: {agent_id}")
        return dict(row)

    def list_agents(self, run_id: str) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM agents WHERE run_id = ? ORDER BY created_at", (run_id,)
            ).fetchall()
        return [dict(row) for row in rows]

    def set_process_state(
        self,
        agent_id: str,
        state: ProcessState,
        *,
        pid: int | None = None,
        exit_code: int | None = None,
        session_id: str | None = None,
    ) -> None:
        fields = ["process_state = ?"]
        values: list[Any] = [state.value]
        if pid is not None:
            fields.append("pid = ?")
            values.append(pid)
        if exit_code is not None:
            fields.append("exit_code = ?")
            values.append(exit_code)
        if session_id is not None:
            fields.append("session_id = ?")
            values.append(session_id)
        if state is ProcessState.RUNNING:
            fields.append("started_at = COALESCE(started_at, ?)")
            values.append(_now())
        if state in {
            ProcessState.DORMANT,
            ProcessState.BLOCKED,
            ProcessState.RETIRED,
            ProcessState.EXITED,
            ProcessState.FAILED,
        }:
            fields.append("pid = NULL")
            fields.append("exited_at = ?")
            values.append(_now())
        if state is ProcessState.RETIRED:
            fields.append("retired_at = COALESCE(retired_at, ?)")
            values.append(_now())
        values.append(agent_id)
        with self._connection() as connection:
            connection.execute(
                f"UPDATE agents SET {', '.join(fields)} WHERE id = ? "
                "AND (process_state != 'retired' OR ? = 'retired')",  # noqa: S608
                values + [state.value],
            )

    def reset_process_observation(self, agent_id: str, *, clear_session: bool = False) -> bool:
        """Clear launcher bookkeeping before an explicit user-requested restart."""

        session_sql = ", session_id = NULL" if clear_session else ""
        with self._connection() as connection:
            changed = connection.execute(
                f"""
                UPDATE agents
                SET process_state = ?, pid = NULL, exit_code = NULL,
                    started_at = NULL, exited_at = NULL{session_sql}
                WHERE id = ? AND process_state != 'retired'
                """,  # noqa: S608
                (ProcessState.CREATED.value, agent_id),
            )
        return bool(changed.rowcount)

    def create_thread(self, run_id: str, author: str, title: str, body: str) -> dict[str, Any]:
        thread_id = _id("thread")
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO threads(id, run_id, author, title, body, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (thread_id, run_id, author, title, body, _now()),
            )
            self._record_activity(
                connection,
                run_id=run_id,
                author=author,
                kind="thread",
                subject_id=thread_id,
                thread_id=thread_id,
                text=f"{title}\n{body}",
            )
        return self.get_thread(thread_id)

    def get_thread(
        self,
        thread_id: str,
        *,
        include_comments: bool = True,
        include_coordination: bool = True,
    ) -> dict[str, Any]:
        with self._connection() as connection:
            connection.execute("BEGIN")
            row = connection.execute("SELECT * FROM threads WHERE id = ?", (thread_id,)).fetchone()
            if row is None:
                raise KeyError(f"unknown thread: {thread_id}")
            result = dict(row)
            event = connection.execute(
                "SELECT id FROM activity WHERE kind = 'thread' AND subject_id = ? ORDER BY id LIMIT 1",
                (thread_id,),
            ).fetchone()
            result["event_id"] = int(event["id"]) if event else None
            comment_filter = "" if include_coordination else f"AND {_discussion_comment_sql('c')}"
            comments = connection.execute(
                f"SELECT c.* FROM comments c WHERE c.thread_id = ? {comment_filter} "
                "ORDER BY c.created_at, c.id",
                (thread_id,),
            ).fetchall() if include_comments else []
            result["comment_count"] = int(connection.execute(
                f"SELECT COUNT(*) FROM comments c WHERE c.thread_id = ? {comment_filter}",
                (thread_id,),
            ).fetchone()[0])
            attachments = connection.execute(
                "SELECT * FROM attachments WHERE thread_id = ? ORDER BY created_at", (thread_id,)
            ).fetchall()
            result["comments"] = self._comment_details(connection, comments)
            result["approach"] = self._approaches_for_threads(connection, [thread_id], preview=False).get(thread_id)
        result["attachments"] = [dict(item) for item in attachments]
        return result

    def get_comment(
        self, thread_id: str, comment_id: str, *, include_coordination: bool = True,
    ) -> dict[str, Any]:
        with self._connection() as connection:
            connection.execute("BEGIN")
            comment_filter = "" if include_coordination else f"AND {_discussion_comment_sql('c')}"
            row = connection.execute(
                f"SELECT c.* FROM comments c WHERE c.thread_id = ? AND c.id = ? {comment_filter}",
                (thread_id, comment_id),
            ).fetchone()
            if row is None:
                raise KeyError(f"unknown comment in thread: {comment_id}")
            return self._comment_details(connection, [row])[0]

    def comments_page(
        self,
        thread_id: str,
        *,
        limit: int = 30,
        after: str | None = None,
        include_coordination: bool = True,
    ) -> dict[str, Any]:
        self._page_limit(limit)
        after = self._keyset_cursor(after)
        with self._connection() as connection:
            connection.execute("BEGIN")
            if connection.execute("SELECT 1 FROM threads WHERE id = ?", (thread_id,)).fetchone() is None:
                raise KeyError(f"unknown thread: {thread_id}")
            parameters: list[Any] = [thread_id]
            clause = ""
            if after:
                cursor = connection.execute(
                    "SELECT created_at, id FROM comments WHERE thread_id = ? AND id = ?",
                    (thread_id, after),
                ).fetchone()
                if cursor is None:
                    raise KeyError(f"unknown comment cursor: {after}")
                clause = "AND (c.created_at > ? OR (c.created_at = ? AND c.id > ?))"
                parameters.extend((cursor["created_at"], cursor["created_at"], cursor["id"]))
            comment_filter = "" if include_coordination else f"AND {_discussion_comment_sql('c')}"
            rows = connection.execute(
                f"SELECT c.* FROM comments c WHERE c.thread_id = ? {clause} {comment_filter} "
                "ORDER BY c.created_at, c.id LIMIT ?",
                (*parameters, limit + 1),
            ).fetchall()
            items = self._comment_details(connection, rows[:limit])
        has_more = len(rows) > limit
        return {"items": items, "next_cursor": items[-1]["id"] if has_more else None,
                "has_more": has_more}

    def list_threads(self, run_id: str, limit: int = 100) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT threads.*,
                       COUNT(DISTINCT comments.id) AS comment_count,
                       COUNT(DISTINCT attachments.id) AS attachment_count
                FROM threads
                LEFT JOIN comments ON comments.thread_id = threads.id
                LEFT JOIN attachments ON attachments.thread_id = threads.id
                WHERE threads.run_id = ?
                GROUP BY threads.id
                ORDER BY threads.created_at DESC
                LIMIT ?
                """,
                (run_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_thread_summaries(
        self,
        run_id: str,
        *,
        limit: int = 30,
        before: str | None = None,
        query: str = "",
        include_coordination: bool = True,
    ) -> tuple[list[dict[str, Any]], str | None]:
        """Return one keyset-paginated page without loading full thread bodies.

        ``before`` is the id of the last thread from the previous page. Threads
        are append-only, so an id is a stable cursor and avoids increasingly
        expensive OFFSET scans as the forum grows.
        """

        if limit < 1 or limit > 100:
            raise ValueError("limit must be between 1 and 100")

        clauses = ["t.run_id = ?"]
        parameters: list[Any] = [run_id]
        comment_filter = "1" if include_coordination else _discussion_comment_sql("c")
        search_comment_filter = (
            "1" if include_coordination else _discussion_comment_sql("search_comments")
        )
        with self._connection() as connection:
            if before:
                cursor = connection.execute(
                    "SELECT created_at, id FROM threads WHERE run_id = ? AND id = ?",
                    (run_id, before),
                ).fetchone()
                if cursor is None:
                    raise KeyError(f"unknown thread cursor: {before}")
                clauses.append("(t.created_at < ? OR (t.created_at = ? AND t.id < ?))")
                parameters.extend((cursor["created_at"], cursor["created_at"], cursor["id"]))

            query = query.strip()
            if query:
                pattern = f"%{query}%"
                clauses.append(
                    """
                    (
                        t.title LIKE ? OR t.body LIKE ? OR EXISTS (
                            SELECT 1 FROM comments search_comments
                            WHERE search_comments.thread_id = t.id
                              AND {search_comment_filter}
                              AND search_comments.body LIKE ?
                        )
                    )
                    """.format(search_comment_filter=search_comment_filter)
                )
                parameters.extend((pattern, pattern, pattern))

            parameters.append(limit + 1)
            rows = connection.execute(
                f"""
                SELECT
                    t.id,
                    t.run_id,
                    t.author,
                    t.title,
                    t.created_at,
                    SUBSTR(t.body, 1, 240) AS preview,
                    LENGTH(t.body) AS body_length,
                    (SELECT COUNT(*) FROM comments c
                     WHERE c.thread_id = t.id AND {comment_filter})
                        AS comment_count,
                    (SELECT COUNT(*) FROM attachments a WHERE a.thread_id = t.id)
                        AS attachment_count,
                    MAX(
                        t.created_at,
                        COALESCE(
                            (SELECT MAX(c.created_at) FROM comments c
                             WHERE c.thread_id = t.id AND {comment_filter}),
                            t.created_at
                        ),
                        COALESCE(
                            (SELECT MAX(a.created_at) FROM attachments a WHERE a.thread_id = t.id),
                            t.created_at
                        )
                    ) AS updated_at
                FROM threads t
                WHERE {' AND '.join(clauses)}
                ORDER BY t.created_at DESC, t.id DESC
                LIMIT ?
                """,  # noqa: S608 - clauses are fixed SQL fragments, values stay parameterized
                parameters,
            ).fetchall()

        has_more = len(rows) > limit
        items = [dict(row) for row in rows[:limit]]
        next_cursor = items[-1]["id"] if has_more and items else None
        return items, next_cursor

    def count_threads(
        self, run_id: str, query: str = "", *, include_coordination: bool = True,
    ) -> int:
        query = query.strip()
        with self._connection() as connection:
            if not query:
                return int(
                    connection.execute(
                        "SELECT COUNT(*) FROM threads WHERE run_id = ?", (run_id,)
                    ).fetchone()[0]
                )
            pattern = f"%{query}%"
            comment_filter = "1" if include_coordination else _discussion_comment_sql("c")
            return int(
                connection.execute(
                    f"""
                    SELECT COUNT(*)
                    FROM threads t
                    WHERE t.run_id = ? AND (
                        t.title LIKE ? OR t.body LIKE ? OR EXISTS (
                            SELECT 1 FROM comments c
                            WHERE c.thread_id = t.id AND {comment_filter} AND c.body LIKE ?
                        )
                    )
                    """,
                    (run_id, pattern, pattern, pattern),
                ).fetchone()[0]
            )

    def add_comment(
        self, thread_id: str, author: str, body: str, *,
        reply_to_event_id: int | None = None, relation: str = "reply",
        artifact_id: str | None = None, validation: str = "",
        evidence_event_ids: Iterable[int] = (),
    ) -> dict[str, Any]:
        with self._connection() as connection:
            # Reference validation, post, notifications, and provenance commit
            # together. The references necessarily predate the new event.
            connection.execute("BEGIN IMMEDIATE")
            return self._add_comment(
                connection, thread_id, author, body,
                reply_to_event_id=reply_to_event_id, relation=relation,
                artifact_id=artifact_id, validation=validation,
                evidence_event_ids=evidence_event_ids,
            )

    def _add_comment(
        self, connection: sqlite3.Connection, thread_id: str, author: str, body: str, *,
        reply_to_event_id: int | None = None, relation: str = "reply",
        artifact_id: str | None = None, validation: str = "",
        evidence_event_ids: Iterable[int] = (), notification_text: str | None = None,
    ) -> dict[str, Any]:
        """Append within the caller's transaction, including related domain records.

        Generated reports can provide empty notification_text so quoted mentions
        do not broadcast again. Chosen subscription delivery remains unchanged.
        """
        comment_id = _id("comment")
        thread = connection.execute("SELECT run_id FROM threads WHERE id = ?", (thread_id,)).fetchone()
        if thread is None:
            raise KeyError(f"unknown thread: {thread_id}")
        metadata = provenance.validate(
            connection, str(thread["run_id"]), author,
            reply_to_event_id=reply_to_event_id, relation=relation,
            artifact_id=artifact_id, validation=validation,
            evidence_event_ids=evidence_event_ids,
        )
        connection.execute(
            "INSERT INTO comments(id, thread_id, author, body, created_at) VALUES (?, ?, ?, ?, ?)",
            (comment_id, thread_id, author, body, _now()),
        )
        event_id = self._record_activity(
            connection, run_id=str(thread["run_id"]), author=author, kind="comment",
            subject_id=comment_id, thread_id=thread_id,
            text=body if notification_text is None else notification_text,
        )
        provenance.record(connection, comment_id, event_id, str(thread["run_id"]), metadata)
        row = connection.execute("SELECT * FROM comments WHERE id = ?", (comment_id,)).fetchone()
        result = provenance.enrich_comments(connection, [row])[0]
        result["run_id"] = str(thread["run_id"])
        return result

    def add_attachment(
        self,
        run_id: str,
        author: str,
        path: str | Path,
        *,
        thread_id: str | None = None,
        description: str = "",
    ) -> dict[str, Any]:
        source = Path(path).expanduser().resolve()
        if not source.is_file():
            raise FileNotFoundError(f"attachment is not a file: {source}")
        digest = hashlib.sha256()
        with source.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        sha256 = digest.hexdigest()
        destination_dir = self.attachments_dir / sha256[:2]
        destination_dir.mkdir(parents=True, exist_ok=True)
        destination = destination_dir / sha256
        if not destination.exists():
            shutil.copy2(source, destination)
        attachment_id = _id("attachment")
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO attachments(
                    id, run_id, thread_id, author, original_name, description,
                    sha256, size, stored_path, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    attachment_id,
                    run_id,
                    thread_id,
                    author,
                    source.name,
                    description,
                    sha256,
                    source.stat().st_size,
                    str(destination),
                    _now(),
                ),
            )
            self._record_activity(
                connection,
                run_id=run_id,
                author=author,
                kind="attachment",
                subject_id=attachment_id,
                thread_id=thread_id,
                text=f"{source.name}\n{description}",
            )
            row = connection.execute(
                "SELECT * FROM attachments WHERE id = ?", (attachment_id,)
            ).fetchone()
        return dict(row)

    def list_attachments(self, run_id: str) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM attachments WHERE run_id = ? ORDER BY created_at DESC",
                (run_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_attachment(self, attachment_id: str) -> dict[str, Any]:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM attachments WHERE id = ?", (attachment_id,)
            ).fetchone()
        if row is None:
            raise KeyError(f"unknown attachment: {attachment_id}")
        return dict(row)

    @staticmethod
    def _page_limit(limit: int, *, maximum: int = 100) -> None:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= maximum:
            raise ValueError(f"limit must be between 1 and {maximum}")

    @staticmethod
    def _keyset_cursor(after: str | None) -> str:
        if after is not None and not isinstance(after, str):
            raise ValueError("after must be a record ID string")
        return after or ""

    @staticmethod
    def _subscription_scope(
        connection: sqlite3.Connection, agent_id: str, thread_id: str
    ) -> None:
        agent = connection.execute("SELECT run_id FROM agents WHERE id = ?", (agent_id,)).fetchone()
        thread = connection.execute("SELECT run_id FROM threads WHERE id = ?", (thread_id,)).fetchone()
        if agent is None:
            raise KeyError(f"unknown agent: {agent_id}")
        if thread is None:
            raise KeyError(f"unknown thread: {thread_id}")
        if agent["run_id"] != thread["run_id"]:
            raise ValueError("subscription thread must belong to the agent's run")

    def subscribe(self, agent_id: str, thread_id: str, *, wake: bool = False) -> dict[str, Any]:
        with self._connection() as connection:
            self._subscription_scope(connection, agent_id, thread_id)
            connection.execute(
                """
                INSERT INTO thread_subscriptions(agent_id, thread_id, wake, created_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(agent_id, thread_id) DO UPDATE SET wake = excluded.wake
                """,
                (agent_id, thread_id, int(bool(wake)), _now()),
            )
            row = connection.execute(
                "SELECT * FROM thread_subscriptions WHERE agent_id = ? AND thread_id = ?",
                (agent_id, thread_id),
            ).fetchone()
        return dict(row) | {"wake": bool(row["wake"])}

    def unsubscribe(self, agent_id: str, thread_id: str) -> dict[str, Any]:
        with self._connection() as connection:
            self._subscription_scope(connection, agent_id, thread_id)
            deleted = connection.execute(
                "DELETE FROM thread_subscriptions WHERE agent_id = ? AND thread_id = ?",
                (agent_id, thread_id),
            )
        return {"agent_id": agent_id, "thread_id": thread_id, "removed": bool(deleted.rowcount)}

    def list_subscriptions(
        self, agent_id: str, *, limit: int = 50, after: str | None = None
    ) -> dict[str, Any]:
        self._page_limit(limit)
        after = self._keyset_cursor(after)
        self.get_agent(agent_id)
        with self._connection() as connection:
            rows = connection.execute(
                """
                WITH interests AS (
                    SELECT thread_id,wake,created_at,1 AS explicit_follow,NULL AS approach_id
                    FROM thread_subscriptions WHERE agent_id=?
                    UNION ALL
                    SELECT a.thread_id,m.wake,m.joined_at,0,a.id
                    FROM approach_members m JOIN approaches a ON a.id=m.approach_id
                    JOIN agents p ON p.id=m.agent_id AND p.run_id=a.run_id
                    WHERE m.agent_id=? AND m.left_at IS NULL AND p.process_state!='retired'
                )
                SELECT ? AS agent_id,i.thread_id,MAX(i.wake) AS wake,MIN(i.created_at) AS created_at,
                       MAX(i.explicit_follow) AS explicit_follow,MAX(i.approach_id) AS approach_id,
                       SUBSTR(t.title,1,240) AS thread_title
                FROM interests i JOIN threads t ON t.id=i.thread_id
                WHERE i.thread_id>? GROUP BY i.thread_id ORDER BY i.thread_id LIMIT ?
                """,
                (agent_id, agent_id, agent_id, after or "", limit + 1),
            ).fetchall()
        items = [dict(row) | {"wake": bool(row["wake"])} for row in rows[:limit]]
        return {"items": items, "next_cursor": items[-1]["thread_id"] if len(rows) > limit else None}

    def search_agents(
        self, run_id: str, *, query: str = "", limit: int = 30, after: str | None = None
    ) -> dict[str, Any]:
        self._page_limit(limit)
        after = self._keyset_cursor(after)
        if not isinstance(query, str):
            raise ValueError("query must be a string")
        self.get_run(run_id)
        # Escape LIKE metacharacters: this is a literal directory search.
        pattern = "%" + query.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT id, run_id, name, provider, model, effort, process_state,
                       created_at, started_at, exited_at, retired_at, retire_reason,
                       participation_state, parked_at
                FROM agents WHERE run_id = ? AND id > ?
                  AND (name LIKE ? ESCAPE '\\' OR model LIKE ? ESCAPE '\\'
                       OR provider LIKE ? ESCAPE '\\')
                ORDER BY id LIMIT ?
                """,
                (run_id, after or "", pattern, pattern, pattern, limit + 1),
            ).fetchall()
        items = [dict(row) for row in rows[:limit]]
        return {"items": items, "next_cursor": items[-1]["id"] if len(rows) > limit else None}

    @staticmethod
    def _approaches_for_threads(
        connection: sqlite3.Connection, thread_ids: Iterable[str], *, preview: bool = True,
    ) -> dict[str, dict[str, Any]]:
        identifiers = list(dict.fromkeys(thread_ids))
        if not identifiers:
            return {}
        rows = connection.execute("""
            SELECT id,thread_id,parent_id,event_id,
                   CASE WHEN ? THEN SUBSTR(hypothesis,1,512) ELSE hypothesis END AS hypothesis,
                   CASE WHEN ? THEN SUBSTR(next_check,1,512) ELSE next_check END AS next_check,
                   LENGTH(hypothesis)>512 AS hypothesis_truncated,LENGTH(next_check)>512 AS next_check_truncated
            FROM approaches WHERE thread_id IN (SELECT value FROM json_each(?))
        """, (int(preview), int(preview), json.dumps(identifiers))).fetchall()
        result = {}
        for row in rows:
            value = dict(row)
            for key in ("hypothesis_truncated", "next_check_truncated"):
                value[key] = preview and bool(value[key])
            result[value["thread_id"]] = value
        return result

    @staticmethod
    def _comment_details(connection: sqlite3.Connection, rows: Iterable[Any]) -> list[dict[str, Any]]:
        from .reports import metadata_for_events

        items = provenance.enrich_comments(connection, rows)
        metadata = metadata_for_events(connection, (item["event_id"] for item in items if item.get("event_id")))
        for item in items:
            item.update(metadata.get(item.get("event_id"), {}))
        return items

    @staticmethod
    def _activity_preview(connection: sqlite3.Connection, item: dict[str, Any]) -> dict[str, Any]:
        return Forum._activity_previews(connection, [item])[0]

    @staticmethod
    def _activity_previews(
        connection: sqlite3.Connection, items: Iterable[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Read a bounded excerpt in SQL; never load the full body for a feed."""
        results = [dict(item) for item in items]
        if not results:
            return []
        ids = [int(item["id"]) for item in results]
        rows = connection.execute(
            """
            SELECT e.id, SUBSTR(t.title, 1, 240) AS thread_title,
                   SUBSTR(CASE e.kind WHEN 'thread' THEN t.body WHEN 'comment' THEN c.body
                          WHEN 'attachment' THEN a.description ELSE '' END, 1, 2000) AS content,
                   LENGTH(CASE e.kind WHEN 'thread' THEN t.body WHEN 'comment' THEN c.body
                          WHEN 'attachment' THEN a.description ELSE '' END) > 2000 AS clipped,
                   SUBSTR(a.original_name, 1, 240) AS attachment_name
            FROM activity e LEFT JOIN threads t ON t.id = e.thread_id
            LEFT JOIN comments c ON e.kind = 'comment' AND c.id = e.subject_id
            LEFT JOIN attachments a ON e.kind = 'attachment' AND a.id = e.subject_id
            WHERE e.id IN (SELECT value FROM json_each(?))
            """,
            (json.dumps(ids),),
        ).fetchall()
        excerpts = {int(row["id"]): row for row in rows}
        metadata = provenance.for_events(connection, ids, preview=True)
        from .reports import metadata_for_events

        reports = metadata_for_events(connection, ids, preview=True)
        approaches = Forum._approaches_for_threads(connection, (item["thread_id"] for item in results if item.get("thread_id")))
        for result in results:
            # Routing is already indexed by recipient. Copying a large audience
            # list into every recipient's prompt amplifies broadcast payloads.
            result.pop("audience_json", None)
            result["event_id"] = int(result["id"])
            result.update(reports.get(result["event_id"], {}))
            row = excerpts.get(result["event_id"])
            if row is not None:
                result.update(thread_title=row["thread_title"], content=row["content"],
                              content_truncated=bool(row["clipped"]))
                if row["attachment_name"] is not None:
                    result["attachment_name"] = row["attachment_name"]
            if result["event_id"] in metadata:
                result["provenance"] = metadata[result["event_id"]]
            if result.get("thread_id") in approaches:
                result["approach"] = approaches[result["thread_id"]]
        return results

    def thread_changes(
        self, thread_id: str, *, after_event: int = 0,
        through_event: int | None = None, limit: int = 30,
    ) -> dict[str, Any]:
        """Read append-only event previews within a frozen, resumable interval.

        Pass the returned through_event on later pages and next_cursor as
        after_event. Full source text remains available through thread reads.
        Reading changes never acknowledges runtime notification delivery.
        """
        self._page_limit(limit)
        provenance.event_id(after_event, "after_event", allow_zero=True)
        if through_event is not None:
            provenance.event_id(through_event, "through_event", allow_zero=True)
        with self._connection() as connection:
            connection.execute("BEGIN")
            thread = connection.execute(
                "SELECT run_id FROM threads WHERE id = ?", (thread_id,),
            ).fetchone()
            if thread is None:
                raise KeyError(f"unknown thread: {thread_id}")
            high_water = int(connection.execute(
                "SELECT COALESCE(MAX(id), 0) FROM activity WHERE run_id = ?",
                (thread["run_id"],),
            ).fetchone()[0])
            # A caller cannot freeze an as-yet-unwritten future range: return
            # the concrete upper bound so later pagination remains reproducible.
            frozen = high_water if through_event is None else min(through_event, high_water)
            rows = connection.execute(
                "SELECT * FROM activity WHERE thread_id = ? AND run_id = ? "
                "AND id > ? AND id <= ? ORDER BY id LIMIT ?",
                (thread_id, thread["run_id"], after_event, frozen, limit + 1),
            ).fetchall()
            items = self._activity_previews(connection, rows[:limit])
        has_more = len(rows) > limit
        return {"items": items, "next_cursor": int(items[-1]["id"]) if has_more else None,
                "through_event": frozen, "has_more": has_more, "content_format": "preview"}

    def activity_page(
        self, agent_id: str, *, scope: str = "following", after: int | None = None, limit: int = 30
    ) -> dict[str, Any]:
        self._page_limit(limit)
        if not isinstance(scope, str) or scope not in {"following", "all"}:
            raise ValueError("activity scope must be 'following' or 'all'")
        if after is not None and (
            isinstance(after, bool) or not isinstance(after, int)
            or not 0 <= after <= 9_223_372_036_854_775_807
        ):
            raise ValueError("after must be a non-negative 64-bit event ID")
        with self._connection() as connection:
            # Freeze both high-water and page in one read snapshot. Events arriving
            # later cannot accidentally advance the returned cursor past a page.
            connection.execute("BEGIN")
            agent = connection.execute(
                "SELECT run_id, name, last_activity_id FROM agents WHERE id = ?", (agent_id,)
            ).fetchone()
            if agent is None:
                raise KeyError(f"unknown agent: {agent_id}")
            cursor = int(agent["last_activity_id"]) if after is None else after
            high_water = int(connection.execute(
                "SELECT COALESCE(MAX(id), 0) FROM activity WHERE run_id = ?", (agent["run_id"],)
            ).fetchone()[0])
            if scope == "following":
                rows = connection.execute(
                    """
                    WITH subscription_events AS (
                        SELECT e.id FROM thread_subscriptions s
                        JOIN activity e ON e.thread_id = s.thread_id
                        WHERE s.agent_id = ? AND e.id > ? AND e.id <= ?
                          AND e.author != ? COLLATE NOCASE
                        ORDER BY e.id LIMIT ?
                    ), approach_events AS (
                        SELECT e.id FROM approach_members m JOIN approaches a ON a.id=m.approach_id
                        JOIN activity e ON e.thread_id=a.thread_id AND e.run_id=a.run_id
                        WHERE m.agent_id=? AND m.left_at IS NULL AND e.id>? AND e.id<=?
                          AND e.author!=? COLLATE NOCASE
                        ORDER BY e.id LIMIT ?
                    ), direct_events AS (
                        SELECT d.event_id AS id FROM notification_deliveries d
                        WHERE d.agent_id = ? AND d.event_id > ? AND d.event_id <= ?
                        ORDER BY d.event_id LIMIT ?
                    ), followed_events AS (
                        SELECT id FROM subscription_events UNION SELECT id FROM approach_events
                        UNION SELECT id FROM direct_events
                    )
                    SELECT e.* FROM followed_events f JOIN activity e ON e.id = f.id
                    WHERE e.run_id = ? AND e.author != ? COLLATE NOCASE
                    ORDER BY e.id LIMIT ?
                    """,
                    (agent_id, cursor, high_water, agent["name"], limit + 1,
                     agent_id, cursor, high_water, agent["name"], limit + 1,
                     agent_id, cursor, high_water, limit + 1,
                     agent["run_id"], agent["name"], limit + 1),
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT e.* FROM activity e
                    WHERE e.run_id = ? AND e.id > ? AND e.id <= ?
                      AND e.author != ? COLLATE NOCASE ORDER BY e.id LIMIT ?
                    """,
                    (agent["run_id"], cursor, high_water, agent["name"], limit + 1),
                ).fetchall()
            items = self._activity_previews(connection, rows[:limit])
        has_more = len(rows) > limit
        through_id = int(items[-1]["id"]) if has_more else max(cursor, high_water)
        return {"items": items, "next_cursor": through_id if has_more else None,
                "through_id": through_id, "has_more": has_more}

    def pending_notifications(
        self, agent_id: str, *, limit: int = 20, after: int = 0, explicit_only: bool = False
    ) -> list[dict[str, Any]]:
        self._page_limit(limit)
        if isinstance(after, bool) or not isinstance(after, int) or not 0 <= after <= 9_223_372_036_854_775_807:
            raise ValueError("after must be a non-negative 64-bit event ID")
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT e.*, d.notification_reason, d.priority
                FROM notification_deliveries d JOIN activity e ON e.id = d.event_id
                WHERE d.agent_id = ? AND d.acknowledged_at IS NULL
                  AND d.withdrawn_at IS NULL
                  AND d.event_id > ?
                  AND (? = 0 OR d.notification_reason IN ('mention', 'broadcast'))
                ORDER BY d.priority, d.event_id LIMIT ?
                """,
                (agent_id, after, int(explicit_only), limit),
            ).fetchall()
            return self._activity_previews(connection, rows)

    def pending_notification_agents(self, run_id: str, *, limit: int = 500) -> list[dict[str, Any]]:
        self._page_limit(limit, maximum=10_000)
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT d.agent_id, MIN(d.priority) AS priority, MIN(d.event_id) AS first_event_id,
                       MAX(d.event_id) AS last_event_id,
                       MAX(CASE WHEN d.notification_reason IN ('mention', 'broadcast')
                                THEN d.event_id ELSE 0 END) AS last_explicit_event_id
                FROM notification_deliveries d JOIN agents p ON p.id = d.agent_id
                WHERE d.run_id = ? AND d.acknowledged_at IS NULL AND p.process_state != 'retired'
                  AND d.withdrawn_at IS NULL
                GROUP BY d.agent_id ORDER BY first_event_id, priority, d.agent_id LIMIT ?
                """,
                (run_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def acknowledge_notifications(
        self, agent_id: str, event_ids: Iterable[int], *, connection: sqlite3.Connection | None = None
    ) -> None:
        ids = sorted(set(int(event_id) for event_id in event_ids))
        if not ids:
            return
        if connection is None:
            with self._connection() as owned:
                self.acknowledge_notifications(agent_id, ids, connection=owned)
            return
        connection.execute(
            """
            UPDATE notification_deliveries SET acknowledged_at = ?
            WHERE agent_id = ? AND acknowledged_at IS NULL
              AND event_id IN (SELECT value FROM json_each(?))
            """,
            (_now(), agent_id, json.dumps(ids)),
        )

    def notification_counts(self, run_id: str) -> dict[str, int]:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) AS pending_events, COUNT(DISTINCT d.agent_id) AS pending_agents
                FROM notification_deliveries d JOIN agents p ON p.id = d.agent_id
                WHERE d.run_id = ? AND d.acknowledged_at IS NULL AND p.process_state != 'retired'
                  AND d.withdrawn_at IS NULL
                """,
                (run_id,),
            ).fetchone()
        return {"pending_events": int(row["pending_events"]), "pending_agents": int(row["pending_agents"])}

    def unseen_activity(self, agent_id: str) -> tuple[list[dict[str, Any]], int]:
        """Return readable external events since this peer's inbox cursor.

        Passive events remain visible here even though they do not wake a
        dormant provider session by themselves.
        """

        with self._connection() as connection:
            agent = connection.execute(
                "SELECT run_id, name, last_activity_id FROM agents WHERE id = ?", (agent_id,)
            ).fetchone()
            if agent is None:
                raise KeyError(f"unknown agent: {agent_id}")
            high_water = int(
                connection.execute(
                    "SELECT COALESCE(MAX(id), 0) FROM activity WHERE run_id = ?",
                    (agent["run_id"],),
                ).fetchone()[0]
            )
            rows = connection.execute(
                """
                SELECT * FROM activity
                WHERE run_id = ? AND id > ?
                ORDER BY id
                """,
                (agent["run_id"], agent["last_activity_id"]),
            ).fetchall()
        visible: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            if item["author"] == agent["name"]:
                continue
            if item.get("notification_mode") == "targeted":
                try:
                    audience = json.loads(item.get("audience_json") or "[]")
                except json.JSONDecodeError:
                    audience = []
                if agent["name"] not in audience:
                    continue
            visible.append(item)
        return visible, high_water

    @staticmethod
    def _notification_reaches(item: dict[str, Any], agent_name: str) -> bool:
        if item["author"] == agent_name:
            return False
        if item.get("notification_mode") == "broadcast":
            return True
        if item.get("notification_mode") != "targeted":
            return False
        try:
            audience = json.loads(item.get("audience_json") or "[]")
        except json.JSONDecodeError:
            return False
        return agent_name in audience

    @staticmethod
    def _enrich_activity(
        connection: sqlite3.Connection, item: dict[str, Any]
    ) -> dict[str, Any]:
        """Attach only the forum data needed to understand a wake trigger."""

        enriched = dict(item)
        enriched["thread_title"] = None
        enriched["content"] = None
        if item["kind"] == "thread":
            row = connection.execute(
                "SELECT title, body FROM threads WHERE id = ?", (item["subject_id"],)
            ).fetchone()
            if row is not None:
                enriched["thread_title"] = str(row["title"])
                enriched["content"] = str(row["body"])
        elif item["kind"] == "comment":
            row = connection.execute(
                """
                SELECT comments.body, threads.title
                FROM comments
                JOIN threads ON threads.id = comments.thread_id
                WHERE comments.id = ?
                """,
                (item["subject_id"],),
            ).fetchone()
            if row is not None:
                enriched["thread_title"] = str(row["title"])
                enriched["content"] = str(row["body"])
        elif item["kind"] == "attachment":
            row = connection.execute(
                """
                SELECT attachments.original_name, attachments.description, threads.title
                FROM attachments
                LEFT JOIN threads ON threads.id = attachments.thread_id
                WHERE attachments.id = ?
                """,
                (item["subject_id"],),
            ).fetchone()
            if row is not None:
                enriched["thread_title"] = (
                    str(row["title"]) if row["title"] is not None else None
                )
                enriched["attachment_name"] = str(row["original_name"])
                enriched["content"] = str(row["description"])
        return enriched

    def wake_events(self, agent_id: str) -> tuple[list[dict[str, Any]], int]:
        """Return explicit mentions which can wake this peer, without consuming them."""

        with self._connection() as connection:
            agent = connection.execute(
                "SELECT run_id, name, last_wake_scan_id FROM agents WHERE id = ?",
                (agent_id,),
            ).fetchone()
            if agent is None:
                raise KeyError(f"unknown agent: {agent_id}")
            high_water = int(
                connection.execute(
                    "SELECT COALESCE(MAX(id), 0) FROM activity WHERE run_id = ?",
                    (agent["run_id"],),
                ).fetchone()[0]
            )
            rows = connection.execute(
                """
                SELECT * FROM activity
                WHERE run_id = ? AND id > ? AND notification_mode != 'passive'
                ORDER BY id
                """,
                (agent["run_id"], agent["last_wake_scan_id"]),
            ).fetchall()
            events: list[dict[str, Any]] = []
            for row in rows:
                item = dict(row)
                if self._notification_reaches(item, str(agent["name"])):
                    events.append(self._enrich_activity(connection, item))
        return events, high_water

    def wake_signal(self, agent_id: str) -> tuple[bool, int]:
        """Check for a new explicit @mention or @all without consuming inbox history."""

        events, high_water = self.wake_events(agent_id)
        return bool(events), high_water

    def resolve_reply_trigger(
        self, agent_id: str, event_id: int | None = None
    ) -> dict[str, Any]:
        """Resolve an explicit mention to the thread where a direct reply belongs."""

        if event_id is not None:
            provenance.event_id(event_id, "event")
        with self._connection() as connection:
            agent = connection.execute(
                "SELECT run_id, name FROM agents WHERE id = ?", (agent_id,)
            ).fetchone()
            if agent is None:
                raise KeyError(f"unknown agent: {agent_id}")
            if event_id is None:
                rows = connection.execute(
                    """
                    SELECT * FROM activity
                    WHERE run_id = ? AND notification_mode != 'passive'
                      AND thread_id IS NOT NULL
                    ORDER BY id DESC
                    """,
                    (agent["run_id"],),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM activity WHERE run_id = ? AND id = ?",
                    (agent["run_id"], event_id),
                ).fetchall()
            for row in rows:
                item = dict(row)
                if item.get("thread_id") and self._notification_reaches(
                    item, str(agent["name"])
                ):
                    return self._enrich_activity(connection, item)
        if event_id is None:
            raise RuntimeError("no explicit mention thread is available for this peer")
        raise ValueError(f"event {event_id} is not an explicit mention for this peer")

    def mark_wake_scanned(self, agent_id: str, through_id: int) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                UPDATE agents
                SET last_wake_scan_id = MAX(last_wake_scan_id, ?)
                WHERE id = ?
                """,
                (through_id, agent_id),
            )

    def mark_activity_seen(self, agent_id: str, through_id: int) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                UPDATE agents
                SET last_activity_id = MAX(last_activity_id, ?),
                    last_wake_scan_id = MAX(last_wake_scan_id, ?)
                WHERE id = ?
                """,
                (through_id, through_id, agent_id),
            )

    def retire_agent(self, agent_id: str, reason: str = "") -> dict[str, Any]:
        with self._connection() as connection:
            agent = connection.execute(
                "SELECT * FROM agents WHERE id = ?", (agent_id,)
            ).fetchone()
            if agent is None:
                raise KeyError(f"unknown agent: {agent_id}")
            now = _now()
            connection.execute(
                """
                UPDATE agents
                SET process_state = ?, retired_at = ?, retire_reason = ?, exited_at = ?
                WHERE id = ?
                """,
                (ProcessState.RETIRED.value, now, reason, now, agent_id),
            )
            self._record_activity(
                connection,
                run_id=str(agent["run_id"]),
                author=str(agent["name"]),
                kind="retire",
                subject_id=agent_id,
                thread_id=None,
                text=reason,
            )
        return self.get_agent(agent_id)

    def list_activity(self, run_id: str, after_id: int = 0) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM activity WHERE run_id = ? AND id > ? ORDER BY id",
                (run_id, after_id),
            ).fetchall()
        return [dict(row) for row in rows]

    def activity_high_water(self, run_id: str) -> int:
        with self._connection() as connection:
            return int(
                connection.execute(
                    "SELECT COALESCE(MAX(id), 0) FROM activity WHERE run_id = ?", (run_id,)
                ).fetchone()[0]
            )

    def activity_summary(
        self, run_id: str, after_id: int = 0, *, include_coordination: bool = True,
    ) -> dict[str, int]:
        """Return a constant-size update signal for browser polling."""

        activity_filter = (
            "1"
            if include_coordination
            else f"(event.kind != 'comment' OR ({_discussion_comment_sql('c')}))"
        )
        with self._connection() as connection:
            high_water = connection.execute(
                "SELECT COALESCE(MAX(id), 0) FROM activity WHERE run_id = ?", (run_id,)
            ).fetchone()[0]
            new_count = connection.execute(
                f"""SELECT COUNT(*) FROM activity event
                    LEFT JOIN comments c ON event.kind='comment' AND c.id=event.subject_id
                    WHERE event.run_id = ? AND event.id > ? AND {activity_filter}""",
                (run_id, after_id),
            ).fetchone()[0]
        return {"high_water": int(high_water), "new_count": int(new_count)}

    @staticmethod
    def _mention_excerpt(
        text: str, aliases: set[str], *, limit: int = 240
    ) -> tuple[str, str] | None:
        match = next(
            (
                candidate
                for candidate in _MENTION_RE.finditer(text)
                if candidate.group(1).casefold() in aliases
            ),
            None,
        )
        if match is None:
            return None
        start = max(0, match.start() - 80)
        end = min(len(text), match.end() + 160)
        excerpt = " ".join(text[start:end].split())
        if start:
            excerpt = "…" + excerpt
        if end < len(text):
            excerpt += "…"
        return excerpt[:limit], f"@{match.group(1)}"

    def human_mentions(
        self,
        run_id: str,
        after_id: int = 0,
        *,
        aliases: Iterable[str] = ("human", "user"),
        limit: int = 20,
        scan_limit: int = 500,
    ) -> dict[str, Any]:
        """Return a bounded stream of new forum mentions directed at the web user."""

        if limit < 1 or limit > 50:
            raise ValueError("limit must be between 1 and 50")
        if scan_limit < limit or scan_limit > 2_000:
            raise ValueError("scan_limit must be between limit and 2000")
        alias_set = {alias.casefold() for alias in aliases if alias.strip()}
        if not alias_set:
            raise ValueError("at least one human mention alias is required")

        with self._connection() as connection:
            high_water = int(
                connection.execute(
                    "SELECT COALESCE(MAX(id), 0) FROM activity WHERE run_id = ?",
                    (run_id,),
                ).fetchone()[0]
            )
            rows = connection.execute(
                """
                SELECT * FROM activity
                WHERE run_id = ? AND id > ?
                ORDER BY id
                LIMIT ?
                """,
                (run_id, after_id, scan_limit),
            ).fetchall()
            cursor = after_id
            mentions: list[dict[str, Any]] = []
            for row in rows:
                item = dict(row)
                cursor = int(item["id"])
                if not item.get("thread_id") or str(item["author"]).casefold() in alias_set:
                    continue
                enriched = self._enrich_activity(connection, item)
                title = str(enriched.get("thread_title") or "")
                content = str(enriched.get("content") or "")
                if item["kind"] == "thread":
                    mention_text = f"{title}\n{content}"
                elif item["kind"] == "attachment":
                    mention_text = f'{enriched.get("attachment_name", "")}\n{content}'
                else:
                    mention_text = content
                excerpt = self._mention_excerpt(mention_text, alias_set)
                if excerpt is None:
                    continue
                preview, mention = excerpt
                mentions.append(
                    {
                        "id": int(item["id"]),
                        "thread_id": str(item["thread_id"]),
                        "thread_title": title[:180],
                        "subject_id": str(item["subject_id"]),
                        "author": str(item["author"]),
                        "kind": str(item["kind"]),
                        "mention": mention,
                        "preview": preview,
                        "created_at": str(item["created_at"]),
                    }
                )
                if len(mentions) >= limit:
                    break
        if not rows:
            cursor = max(after_id, high_water)
        return {
            "items": mentions,
            "cursor": int(cursor),
            "has_more": int(cursor) < high_water,
        }

    def run_statistics(
        self, run_id: str, *, include_coordination: bool = True,
    ) -> dict[str, int]:
        comment_filter = "1" if include_coordination else _discussion_comment_sql("c")
        with self._connection() as connection:
            row = connection.execute(
                f"""
                SELECT
                    (SELECT COUNT(*) FROM threads WHERE run_id = ?) AS thread_count,
                    (
                        SELECT COUNT(*)
                        FROM comments c
                        JOIN threads t ON t.id = c.thread_id
                        WHERE t.run_id = ? AND {comment_filter}
                    ) AS comment_count,
                    (SELECT COUNT(*) FROM attachments WHERE run_id = ?) AS attachment_count
                """,
                (run_id, run_id, run_id),
            ).fetchone()
        return {key: int(row[key]) for key in row.keys()}

    def search(self, run_id: str, query: str) -> list[dict[str, Any]]:
        pattern = f"%{query}%"
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT DISTINCT threads.*
                FROM threads
                LEFT JOIN comments ON comments.thread_id = threads.id
                WHERE threads.run_id = ?
                  AND (
                    threads.title LIKE ? OR threads.body LIKE ? OR comments.body LIKE ?
                  )
                ORDER BY threads.created_at DESC
                """,
                (run_id, pattern, pattern, pattern),
            ).fetchall()
        return [dict(row) for row in rows]

    def snapshot(self, run_id: str) -> dict[str, Any]:
        return {
            "run": self.get_run(run_id),
            "agents": self.list_agents(run_id),
            "threads": [self.get_thread(item["id"]) for item in self.list_threads(run_id)],
            "attachments": self.list_attachments(run_id),
            "activity_high_water": self.activity_high_water(run_id),
        }

    @staticmethod
    def to_json(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, indent=2)


def resolve_run_id(forum: Forum, run_id: str | None) -> str:
    if run_id:
        forum.get_run(run_id)
        return run_id
    latest = forum.latest_run()
    if latest is None:
        raise RuntimeError("no IDEA run exists yet")
    return str(latest["id"])


def print_rows(rows: Iterable[dict[str, Any]]) -> str:
    return "\n".join(json.dumps(row, ensure_ascii=False) for row in rows)
