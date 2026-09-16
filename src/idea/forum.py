from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable

from .domain import AgentProfile, ProcessState


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class Forum:
    """An append-only knowledge board shared by the fixed peer set."""

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
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS threads_by_run ON threads(run_id, created_at);
                CREATE INDEX IF NOT EXISTS comments_by_thread ON comments(thread_id, created_at, id);
                CREATE INDEX IF NOT EXISTS attachments_by_run ON attachments(run_id, created_at);
                CREATE INDEX IF NOT EXISTS activity_by_run ON activity(run_id, id);
                CREATE INDEX IF NOT EXISTS agents_by_run_id ON agents(run_id, id);
                """
            )

    def _record_activity(
        self, connection: sqlite3.Connection, *, run_id: str, author: str,
        kind: str, subject_id: str, thread_id: str | None,
    ) -> int:
        return int(connection.execute(
            """INSERT INTO activity(run_id,author,kind,subject_id,thread_id,created_at)
               VALUES (?,?,?,?,?,?)""",
            (run_id, author, kind, subject_id, thread_id, _now()),
        ).lastrowid)

    def create_run(self, goal: str, workspace: str | Path) -> dict[str, Any]:
        run_id = _id("run")
        with self._connection() as connection:
            connection.execute(
                "INSERT INTO runs(id,goal,workspace,created_at) VALUES (?,?,?,?)",
                (run_id, goal, str(Path(workspace).expanduser().resolve()), _now()),
            )
        return self.get_run(run_id)

    def get_run(self, run_id: str) -> dict[str, Any]:
        with self._connection() as connection:
            row = connection.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            raise KeyError(f"unknown run: {run_id}")
        return dict(row)

    def latest_run(self) -> dict[str, Any] | None:
        with self._connection() as connection:
            row = connection.execute("SELECT * FROM runs ORDER BY created_at DESC LIMIT 1").fetchone()
        return dict(row) if row is not None else None

    def list_runs(self) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute("SELECT * FROM runs ORDER BY created_at DESC").fetchall()
        return [dict(row) for row in rows]

    def register_agent(self, run_id: str, profile: AgentProfile) -> dict[str, Any]:
        self.get_run(run_id)
        agent_id = _id("agent")
        with self._connection() as connection:
            connection.execute(
                """INSERT INTO agents(id,run_id,name,provider,model,effort,process_state,created_at)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (agent_id, run_id, profile.name, profile.provider.value, profile.model,
                 profile.effort.value, ProcessState.CREATED.value, _now()),
            )
        return self.get_agent(agent_id)

    def get_agent(self, agent_id: str) -> dict[str, Any]:
        with self._connection() as connection:
            row = connection.execute("SELECT * FROM agents WHERE id=?", (agent_id,)).fetchone()
        if row is None:
            raise KeyError(f"unknown agent: {agent_id}")
        return dict(row)

    def list_agents(self, run_id: str) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute("SELECT * FROM agents WHERE run_id=? ORDER BY created_at,id", (run_id,)).fetchall()
        return [dict(row) for row in rows]

    def set_process_state(
        self, agent_id: str, state: ProcessState, *, pid: int | None = None,
        exit_code: int | None = None, session_id: str | None = None,
    ) -> None:
        fields = ["process_state=?"]
        values: list[Any] = [state.value]
        if pid is not None:
            fields.append("pid=?")
            values.append(pid)
        if exit_code is not None:
            fields.append("exit_code=?")
            values.append(exit_code)
        if session_id is not None:
            fields.append("session_id=?")
            values.append(session_id)
        if state is ProcessState.RUNNING:
            fields.append("started_at=COALESCE(started_at,?)")
            values.append(_now())
        if state in {ProcessState.DORMANT, ProcessState.BLOCKED, ProcessState.EXITED, ProcessState.FAILED}:
            fields.extend(("pid=NULL", "exited_at=?"))
            values.append(_now())
        values.append(agent_id)
        with self._connection() as connection:
            connection.execute(f"UPDATE agents SET {', '.join(fields)} WHERE id=?", values)  # noqa: S608

    def reset_process_observation(self, agent_id: str, *, clear_session: bool = False) -> bool:
        session = ", session_id=NULL" if clear_session else ""
        with self._connection() as connection:
            changed = connection.execute(
                f"""UPDATE agents SET process_state=?,pid=NULL,exit_code=NULL,
                    started_at=NULL,exited_at=NULL{session} WHERE id=?""",  # noqa: S608
                (ProcessState.CREATED.value, agent_id),
            )
        return bool(changed.rowcount)

    def create_thread(self, run_id: str, author: str, title: str, body: str) -> dict[str, Any]:
        self.get_run(run_id)
        thread_id = _id("thread")
        with self._connection() as connection:
            connection.execute(
                "INSERT INTO threads(id,run_id,author,title,body,created_at) VALUES (?,?,?,?,?,?)",
                (thread_id, run_id, author, title, body, _now()),
            )
            self._record_activity(connection, run_id=run_id, author=author, kind="thread",
                                  subject_id=thread_id, thread_id=thread_id)
        return self.get_thread(thread_id)

    @staticmethod
    def _comment_details(connection: sqlite3.Connection, rows: Iterable[sqlite3.Row]) -> list[dict[str, Any]]:
        items = [dict(row) for row in rows]
        if not items:
            return []
        event_rows = connection.execute(
            """SELECT id,subject_id FROM activity WHERE kind='comment'
               AND subject_id IN (SELECT value FROM json_each(?))""",
            (json.dumps([item["id"] for item in items]),),
        ).fetchall()
        events = {str(row["subject_id"]): int(row["id"]) for row in event_rows}
        for item in items:
            item["event_id"] = events.get(str(item["id"]))
        return items

    def get_thread(self, thread_id: str, *, include_comments: bool = True) -> dict[str, Any]:
        with self._connection() as connection:
            row = connection.execute("SELECT * FROM threads WHERE id=?", (thread_id,)).fetchone()
            if row is None:
                raise KeyError(f"unknown thread: {thread_id}")
            result = dict(row)
            event = connection.execute(
                "SELECT id FROM activity WHERE kind='thread' AND subject_id=? ORDER BY id LIMIT 1", (thread_id,)
            ).fetchone()
            result["event_id"] = int(event["id"]) if event else None
            comments = connection.execute(
                "SELECT * FROM comments WHERE thread_id=? AND author!='system' ORDER BY created_at,id", (thread_id,)
            ).fetchall() if include_comments else []
            result["comments"] = self._comment_details(connection, comments)
            result["comment_count"] = int(connection.execute(
                "SELECT COUNT(*) FROM comments WHERE thread_id=? AND author!='system'", (thread_id,)
            ).fetchone()[0])
            result["attachments"] = [dict(item) for item in connection.execute(
                "SELECT * FROM attachments WHERE thread_id=? ORDER BY created_at,id", (thread_id,)
            ).fetchall()]
        return result

    def get_comment(self, thread_id: str, comment_id: str) -> dict[str, Any]:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM comments WHERE thread_id=? AND id=? AND author!='system'", (thread_id, comment_id)
            ).fetchone()
            if row is None:
                raise KeyError(f"unknown comment in thread: {comment_id}")
            return self._comment_details(connection, [row])[0]

    @staticmethod
    def _page_limit(limit: int, *, maximum: int = 100) -> None:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= maximum:
            raise ValueError(f"limit must be between 1 and {maximum}")

    def comments_page(self, thread_id: str, *, limit: int = 30, after: str | None = None) -> dict[str, Any]:
        self._page_limit(limit)
        with self._connection() as connection:
            if connection.execute("SELECT 1 FROM threads WHERE id=?", (thread_id,)).fetchone() is None:
                raise KeyError(f"unknown thread: {thread_id}")
            values: list[Any] = [thread_id]
            cursor_clause = ""
            if after:
                cursor = connection.execute(
                    "SELECT created_at,id FROM comments WHERE thread_id=? AND id=?", (thread_id, after)
                ).fetchone()
                if cursor is None:
                    raise KeyError(f"unknown comment cursor: {after}")
                cursor_clause = "AND (created_at>? OR (created_at=? AND id>?))"
                values.extend((cursor["created_at"], cursor["created_at"], cursor["id"]))
            rows = connection.execute(
                f"""SELECT * FROM comments WHERE thread_id=? AND author!='system' {cursor_clause}
                    ORDER BY created_at,id LIMIT ?""",  # noqa: S608
                (*values, limit + 1),
            ).fetchall()
            items = self._comment_details(connection, rows[:limit])
        return {"items": items, "next_cursor": items[-1]["id"] if len(rows) > limit else None,
                "has_more": len(rows) > limit}

    def list_threads(self, run_id: str, limit: int = 100) -> list[dict[str, Any]]:
        self._page_limit(limit)
        with self._connection() as connection:
            rows = connection.execute(
                """SELECT t.*,COUNT(DISTINCT c.id) AS comment_count,COUNT(DISTINCT a.id) AS attachment_count
                   FROM threads t LEFT JOIN comments c ON c.thread_id=t.id AND c.author!='system'
                   LEFT JOIN attachments a ON a.thread_id=t.id WHERE t.run_id=?
                   GROUP BY t.id ORDER BY t.created_at DESC,t.id DESC LIMIT ?""",
                (run_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_thread_summaries(self, run_id: str, *, limit: int = 30, before: str | None = None,
                              query: str = "") -> tuple[list[dict[str, Any]], str | None]:
        self._page_limit(limit)
        clauses = ["t.run_id=?"]
        values: list[Any] = [run_id]
        with self._connection() as connection:
            if before:
                cursor = connection.execute(
                    "SELECT created_at,id FROM threads WHERE run_id=? AND id=?", (run_id, before)
                ).fetchone()
                if cursor is None:
                    raise KeyError(f"unknown thread cursor: {before}")
                clauses.append("(t.created_at<? OR (t.created_at=? AND t.id<?))")
                values.extend((cursor["created_at"], cursor["created_at"], cursor["id"]))
            if query.strip():
                pattern = f"%{query.strip()}%"
                clauses.append("""(t.title LIKE ? OR t.body LIKE ? OR EXISTS (
                    SELECT 1 FROM comments c WHERE c.thread_id=t.id AND c.author!='system' AND c.body LIKE ?))""")
                values.extend((pattern, pattern, pattern))
            rows = connection.execute(
                f"""SELECT t.id,t.run_id,t.author,t.title,t.created_at,SUBSTR(t.body,1,240) AS preview,
                    LENGTH(t.body) AS body_length,
                    (SELECT COUNT(*) FROM comments c WHERE c.thread_id=t.id AND c.author!='system') AS comment_count,
                    (SELECT COUNT(*) FROM attachments a WHERE a.thread_id=t.id) AS attachment_count
                    FROM threads t WHERE {' AND '.join(clauses)} ORDER BY t.created_at DESC,t.id DESC LIMIT ?""",  # noqa: S608
                (*values, limit + 1),
            ).fetchall()
        items = [dict(row) for row in rows[:limit]]
        return items, items[-1]["id"] if len(rows) > limit else None

    def count_threads(self, run_id: str, query: str = "") -> int:
        if not query.strip():
            with self._connection() as connection:
                return int(connection.execute("SELECT COUNT(*) FROM threads WHERE run_id=?", (run_id,)).fetchone()[0])
        pattern = f"%{query.strip()}%"
        with self._connection() as connection:
            return int(connection.execute(
                """SELECT COUNT(*) FROM threads t WHERE t.run_id=? AND
                   (t.title LIKE ? OR t.body LIKE ? OR EXISTS (
                     SELECT 1 FROM comments c WHERE c.thread_id=t.id AND c.author!='system' AND c.body LIKE ?))""",
                (run_id, pattern, pattern, pattern),
            ).fetchone()[0])

    def add_comment(self, thread_id: str, author: str, body: str) -> dict[str, Any]:
        comment_id = _id("comment")
        with self._connection() as connection:
            thread = connection.execute("SELECT run_id FROM threads WHERE id=?", (thread_id,)).fetchone()
            if thread is None:
                raise KeyError(f"unknown thread: {thread_id}")
            connection.execute(
                "INSERT INTO comments(id,thread_id,author,body,created_at) VALUES (?,?,?,?,?)",
                (comment_id, thread_id, author, body, _now()),
            )
            event_id = self._record_activity(connection, run_id=str(thread["run_id"]), author=author,
                                             kind="comment", subject_id=comment_id, thread_id=thread_id)
            row = connection.execute("SELECT * FROM comments WHERE id=?", (comment_id,)).fetchone()
        result = dict(row)
        result["run_id"] = str(thread["run_id"])
        result["event_id"] = event_id
        return result

    def add_attachment(self, run_id: str, author: str, path: str | Path, *, thread_id: str | None = None,
                       description: str = "") -> dict[str, Any]:
        self.get_run(run_id)
        source = Path(path).expanduser().resolve()
        if not source.is_file():
            raise FileNotFoundError(f"attachment is not a file: {source}")
        if thread_id and self.get_thread(thread_id, include_comments=False)["run_id"] != run_id:
            raise ValueError("thread belongs to another run")
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        destination = self.attachments_dir / digest[:2] / digest
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists():
            shutil.copy2(source, destination)
        attachment_id = _id("attachment")
        with self._connection() as connection:
            connection.execute(
                """INSERT INTO attachments(id,run_id,thread_id,author,original_name,description,sha256,size,stored_path,created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (attachment_id, run_id, thread_id, author, source.name, description, digest,
                 source.stat().st_size, str(destination), _now()),
            )
            self._record_activity(connection, run_id=run_id, author=author, kind="attachment",
                                  subject_id=attachment_id, thread_id=thread_id)
            row = connection.execute("SELECT * FROM attachments WHERE id=?", (attachment_id,)).fetchone()
        return dict(row)

    def list_attachments(self, run_id: str) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute("SELECT * FROM attachments WHERE run_id=? ORDER BY created_at DESC,id DESC", (run_id,)).fetchall()
        return [dict(row) for row in rows]

    def get_attachment(self, attachment_id: str) -> dict[str, Any]:
        with self._connection() as connection:
            row = connection.execute("SELECT * FROM attachments WHERE id=?", (attachment_id,)).fetchone()
        if row is None:
            raise KeyError(f"unknown attachment: {attachment_id}")
        return dict(row)

    def activity_high_water(self, run_id: str) -> int:
        with self._connection() as connection:
            return int(connection.execute(
                "SELECT COALESCE(MAX(id),0) FROM activity WHERE run_id=?", (run_id,)
            ).fetchone()[0])

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
