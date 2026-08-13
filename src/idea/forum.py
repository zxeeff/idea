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


_MENTION_RE = re.compile(r"(?<![\w-])@([A-Za-z0-9][A-Za-z0-9_-]*)(?![\w-])")


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
                """
            )
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

    @staticmethod
    def _activity_notification(
        connection: sqlite3.Connection, run_id: str, text: str
    ) -> tuple[str, str | None]:
        tokens = {token.casefold() for token in _MENTION_RE.findall(text)}
        if "all" in tokens:
            return "broadcast", None
        names = [
            str(row["name"])
            for row in connection.execute(
                "SELECT name FROM agents WHERE run_id = ?", (run_id,)
            ).fetchall()
        ]
        mentioned = [name for name in names if name.casefold() in tokens]
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
    ) -> None:
        notification_mode, audience_json = self._activity_notification(
            connection, run_id, text
        )
        connection.execute(
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
                f"UPDATE agents SET {', '.join(fields)} WHERE id = ?",  # noqa: S608
                values,
            )

    def reset_process_observation(self, agent_id: str, *, clear_session: bool = False) -> None:
        """Clear launcher bookkeeping before an explicit user-requested restart."""

        session_sql = ", session_id = NULL" if clear_session else ""
        with self._connection() as connection:
            connection.execute(
                f"""
                UPDATE agents
                SET process_state = ?, pid = NULL, exit_code = NULL,
                    started_at = NULL, exited_at = NULL{session_sql}
                WHERE id = ?
                """,  # noqa: S608
                (ProcessState.CREATED.value, agent_id),
            )

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

    def get_thread(self, thread_id: str) -> dict[str, Any]:
        with self._connection() as connection:
            row = connection.execute("SELECT * FROM threads WHERE id = ?", (thread_id,)).fetchone()
            if row is None:
                raise KeyError(f"unknown thread: {thread_id}")
            result = dict(row)
            comments = connection.execute(
                "SELECT * FROM comments WHERE thread_id = ? ORDER BY created_at", (thread_id,)
            ).fetchall()
            attachments = connection.execute(
                "SELECT * FROM attachments WHERE thread_id = ? ORDER BY created_at", (thread_id,)
            ).fetchall()
        result["comments"] = [dict(comment) for comment in comments]
        result["attachments"] = [dict(item) for item in attachments]
        return result

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
                              AND search_comments.body LIKE ?
                        )
                    )
                    """
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
                    (SELECT COUNT(*) FROM comments c WHERE c.thread_id = t.id)
                        AS comment_count,
                    (SELECT COUNT(*) FROM attachments a WHERE a.thread_id = t.id)
                        AS attachment_count,
                    MAX(
                        t.created_at,
                        COALESCE(
                            (SELECT MAX(c.created_at) FROM comments c WHERE c.thread_id = t.id),
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

    def count_threads(self, run_id: str, query: str = "") -> int:
        query = query.strip()
        with self._connection() as connection:
            if not query:
                return int(
                    connection.execute(
                        "SELECT COUNT(*) FROM threads WHERE run_id = ?", (run_id,)
                    ).fetchone()[0]
                )
            pattern = f"%{query}%"
            return int(
                connection.execute(
                    """
                    SELECT COUNT(*)
                    FROM threads t
                    WHERE t.run_id = ? AND (
                        t.title LIKE ? OR t.body LIKE ? OR EXISTS (
                            SELECT 1 FROM comments c
                            WHERE c.thread_id = t.id AND c.body LIKE ?
                        )
                    )
                    """,
                    (run_id, pattern, pattern, pattern),
                ).fetchone()[0]
            )

    def add_comment(self, thread_id: str, author: str, body: str) -> dict[str, Any]:
        comment_id = _id("comment")
        with self._connection() as connection:
            thread = connection.execute(
                "SELECT run_id FROM threads WHERE id = ?", (thread_id,)
            ).fetchone()
            if thread is None:
                raise KeyError(f"unknown thread: {thread_id}")
            connection.execute(
                """
                INSERT INTO comments(id, thread_id, author, body, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (comment_id, thread_id, author, body, _now()),
            )
            self._record_activity(
                connection,
                run_id=str(thread["run_id"]),
                author=author,
                kind="comment",
                subject_id=comment_id,
                thread_id=thread_id,
                text=body,
            )
            row = connection.execute("SELECT * FROM comments WHERE id = ?", (comment_id,)).fetchone()
        result = dict(row)
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

    def activity_summary(self, run_id: str, after_id: int = 0) -> dict[str, int]:
        """Return a constant-size update signal for browser polling."""

        with self._connection() as connection:
            high_water = connection.execute(
                "SELECT COALESCE(MAX(id), 0) FROM activity WHERE run_id = ?", (run_id,)
            ).fetchone()[0]
            new_count = connection.execute(
                "SELECT COUNT(*) FROM activity WHERE run_id = ? AND id > ?",
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

    def run_statistics(self, run_id: str) -> dict[str, int]:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT
                    (SELECT COUNT(*) FROM threads WHERE run_id = ?) AS thread_count,
                    (
                        SELECT COUNT(*)
                        FROM comments c
                        JOIN threads t ON t.id = c.thread_id
                        WHERE t.run_id = ?
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
