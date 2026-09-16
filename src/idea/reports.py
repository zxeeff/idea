"""Immutable, source-linked reports exchanged between public approach threads."""

from __future__ import annotations

import json
import sqlite3
import uuid
from itertools import islice
from typing import TYPE_CHECKING, Any, Iterable

from . import provenance

if TYPE_CHECKING:
    from .forum import Forum


def initialize(connection: sqlite3.Connection) -> None:
    connection.executescript("""
        CREATE TABLE IF NOT EXISTS approach_reports (
            id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL REFERENCES runs(id),
            approach_id TEXT NOT NULL REFERENCES approaches(id),
            thread_id TEXT NOT NULL REFERENCES threads(id),
            comment_id TEXT NOT NULL UNIQUE REFERENCES comments(id),
            event_id INTEGER NOT NULL UNIQUE REFERENCES activity(id),
            author TEXT NOT NULL,
            summary TEXT NOT NULL,
            conditions TEXT NOT NULL,
            open_questions TEXT NOT NULL,
            artifact_id TEXT,
            artifact_base_revision TEXT,
            artifact_patch_sha256 TEXT,
            supersedes_report_id TEXT REFERENCES approach_reports(id),
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS reports_by_run ON approach_reports(run_id, event_id, id);
        CREATE INDEX IF NOT EXISTS reports_by_approach ON approach_reports(approach_id, event_id, id);
        CREATE INDEX IF NOT EXISTS reports_by_thread ON approach_reports(thread_id, event_id, id);
        CREATE INDEX IF NOT EXISTS reports_by_predecessor ON approach_reports(supersedes_report_id, event_id);
        CREATE TABLE IF NOT EXISTS report_event_references (
            report_id TEXT NOT NULL REFERENCES approach_reports(id),
            event_id INTEGER NOT NULL REFERENCES activity(id),
            kind TEXT NOT NULL CHECK(kind IN ('source', 'validation')),
            position INTEGER NOT NULL,
            PRIMARY KEY(report_id, kind, event_id)
        );
        CREATE INDEX IF NOT EXISTS reports_by_source ON report_event_references(event_id, report_id);
        CREATE TABLE IF NOT EXISTS report_exchanges (
            id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL REFERENCES runs(id),
            report_id TEXT NOT NULL REFERENCES approach_reports(id),
            target_approach_id TEXT NOT NULL REFERENCES approaches(id),
            thread_id TEXT NOT NULL REFERENCES threads(id),
            comment_id TEXT NOT NULL UNIQUE REFERENCES comments(id),
            event_id INTEGER NOT NULL UNIQUE REFERENCES activity(id),
            author TEXT NOT NULL,
            application TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(report_id, target_approach_id)
        );
        CREATE INDEX IF NOT EXISTS exchanges_by_run ON report_exchanges(run_id, event_id);
    """)


def _text(value: Any, name: str, maximum: int, *, required: bool = False) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be text")
    try:
        size = len(value.encode("utf-8"))
    except UnicodeError as error:
        raise ValueError(f"{name} must be valid UTF-8") from error
    if size > maximum or (required and not value.strip()):
        raise ValueError(f"{name} must {'be nonempty and ' if required else ''}fit within {maximum} UTF-8 bytes")
    return value


def _ids(values: Iterable[int], name: str, *, required: bool = False) -> list[int]:
    if isinstance(values, (str, bytes, dict)):
        raise ValueError(f"{name} must contain event IDs")
    try:
        result = list(islice(iter(values), 17))
    except TypeError as error:
        raise ValueError(f"{name} must contain event IDs") from error
    if len(result) > 16 or (required and not result):
        raise ValueError(f"{name} requires {'1 to' if required else 'at most'} 16 events")
    return list(dict.fromkeys(provenance.event_id(value, name) for value in result))


def _clip(value: str, maximum: int = 512) -> tuple[str, bool]:
    encoded = value.encode("utf-8")
    return (encoded[:maximum].decode("utf-8", errors="ignore"), True) if len(encoded) > maximum else (value, False)


def _approach(connection: sqlite3.Connection, run_id: str, identifier: str) -> sqlite3.Row:
    _text(identifier, "approach_id", 200, required=True)
    row = connection.execute("SELECT id, run_id, thread_id FROM approaches WHERE id = ?", (identifier,)).fetchone()
    if row is None:
        raise KeyError(f"unknown approach: {identifier}")
    if row["run_id"] != run_id:
        raise ValueError("approach belongs to another run")
    return row


def _report_rows(
    connection: sqlite3.Connection, clause: str, parameters: Iterable[Any], *,
    preview: bool, suffix: str = "",
) -> list[sqlite3.Row]:
    # Clauses and suffixes are private, fixed SQL fragments; all values are bound.
    return connection.execute(f"""
        SELECT r.id, r.run_id, r.approach_id, r.thread_id, r.comment_id, r.event_id,
               r.author, r.artifact_id, r.artifact_base_revision, r.artifact_patch_sha256,
               r.supersedes_report_id, r.created_at,
               CASE WHEN ? THEN SUBSTR(r.summary,1,512) ELSE r.summary END AS summary,
               CASE WHEN ? THEN SUBSTR(r.conditions,1,512) ELSE r.conditions END AS conditions,
               CASE WHEN ? THEN SUBSTR(r.open_questions,1,512) ELSE r.open_questions END AS open_questions,
               LENGTH(r.summary) AS summary_length, LENGTH(r.conditions) AS conditions_length,
               LENGTH(r.open_questions) AS open_questions_length
        FROM approach_reports r WHERE {clause} {suffix}
    """, (int(preview), int(preview), int(preview), *parameters)).fetchall()


def _event_reference(row: sqlite3.Row) -> dict[str, Any]:
    return {"event_id": int(row["id"]), "thread_id": row["thread_id"],
            "subject_id": row["subject_id"], "kind": row["kind"], "author": row["author"]}


def _source_changes(connection: sqlite3.Connection, report_ids: list[str]) -> dict[str, dict]:
    # Include existing objections as well as new ones. A report must not hide a
    # known challenge merely by being published after it; after_report distinguishes them.
    rows = connection.execute("""
        WITH selected AS (
            SELECT id, run_id, event_id FROM approach_reports
            WHERE id IN (SELECT value FROM json_each(?))
        ), targets AS (
            SELECT id AS report_id, event_id AS target_id FROM selected
            UNION SELECT rr.report_id, rr.event_id FROM report_event_references rr
                JOIN selected s ON s.id = rr.report_id
        ), changes AS (
            SELECT s.id AS report_id, s.event_id AS report_event_id,
                   e.id, e.thread_id, e.subject_id, e.kind, SUBSTR(e.author,1,240) AS author,
                   cp.relation, cp.reply_to_event_id
            FROM targets t JOIN selected s ON s.id = t.report_id
            JOIN comment_provenance cp ON cp.reply_to_event_id = t.target_id AND cp.run_id = s.run_id
            JOIN activity e ON e.id = cp.event_id
            WHERE cp.relation IN ('challenges','retracts','supersedes')
        ), ranked AS (
            SELECT *, COUNT(*) OVER (PARTITION BY report_id) AS total_count,
                   ROW_NUMBER() OVER (PARTITION BY report_id ORDER BY id) AS oldest,
                   ROW_NUMBER() OVER (PARTITION BY report_id ORDER BY id DESC) AS latest FROM changes
        ) SELECT * FROM ranked WHERE oldest <= 4 OR latest <= 4 ORDER BY report_id, id
    """, (json.dumps(report_ids),)).fetchall()
    result = {identifier: {"total_count": 0, "items": []} for identifier in report_ids}
    for row in rows:
        value = result[row["report_id"]]
        value["total_count"] = int(row["total_count"])
        value["items"].append(_event_reference(row) | {
            "relation": row["relation"], "reply_to_event_id": int(row["reply_to_event_id"]),
            "after_report": int(row["id"]) > int(row["report_event_id"]),
        })
    return result


def _superseding(connection: sqlite3.Connection, report_ids: list[str]) -> dict[str, dict]:
    rows = connection.execute("""
        WITH ranked AS (
            SELECT id, event_id, thread_id, approach_id, author, supersedes_report_id,
                   COUNT(*) OVER (PARTITION BY supersedes_report_id) AS total_count,
                   ROW_NUMBER() OVER (PARTITION BY supersedes_report_id ORDER BY event_id) AS oldest,
                   ROW_NUMBER() OVER (PARTITION BY supersedes_report_id ORDER BY event_id DESC) AS latest
            FROM approach_reports WHERE supersedes_report_id IN (SELECT value FROM json_each(?))
        ) SELECT * FROM ranked WHERE oldest <= 4 OR latest <= 4 ORDER BY event_id
    """, (json.dumps(report_ids),)).fetchall()
    result = {identifier: {"total_count": 0, "items": []} for identifier in report_ids}
    for row in rows:
        value = result[row["supersedes_report_id"]]
        value["total_count"] = int(row["total_count"])
        value["items"].append({key: row[key] for key in ("id", "event_id", "thread_id", "approach_id", "author")})
    return result


def _materialize_reports(
    connection: sqlite3.Connection, rows: Iterable[sqlite3.Row], *, preview: bool,
) -> list[dict[str, Any]]:
    rows = list(rows)
    if not rows:
        return []
    identifiers = [row["id"] for row in rows]
    references = connection.execute("""
        SELECT rr.report_id, rr.kind AS reference_kind, e.id, e.thread_id, e.subject_id,
               e.kind, SUBSTR(e.author,1,240) AS author, cp.relation, cp.reply_to_event_id,
               cp.artifact_id, cp.artifact_base_revision, cp.artifact_patch_sha256,
               SUBSTR(cp.validation,1,512) AS validation, LENGTH(cp.validation) AS validation_length
        FROM report_event_references rr JOIN activity e ON e.id = rr.event_id
        LEFT JOIN comment_provenance cp ON cp.event_id = e.id
        WHERE rr.report_id IN (SELECT value FROM json_each(?))
        ORDER BY rr.report_id, rr.kind, rr.position
    """, (json.dumps(identifiers),)).fetchall()
    by_report = {identifier: {"source": [], "validation": []} for identifier in identifiers}
    for row in references:
        item = _event_reference(row)
        if row["reference_kind"] == "validation":
            validation, clipped = _clip(row["validation"] or "")
            item.update(relation=row["relation"], reply_to_event_id=row["reply_to_event_id"],
                        artifact_id=row["artifact_id"], validation=validation,
                        validation_truncated=clipped or len(validation) < int(row["validation_length"] or 0))
            if row["artifact_id"]:
                item["artifact"] = {"id": row["artifact_id"], "base_revision": row["artifact_base_revision"],
                                    "patch_sha256": row["artifact_patch_sha256"]}
        by_report[row["report_id"]][row["reference_kind"]].append(item)
    changes = _source_changes(connection, identifiers)
    superseding = _superseding(connection, identifiers)
    result = []
    for row in rows:
        value = {key: row[key] for key in ("id", "run_id", "approach_id", "thread_id", "comment_id", "event_id",
                                         "author", "artifact_id", "supersedes_report_id", "created_at")}
        truncated = False
        for name in ("summary", "conditions", "open_questions"):
            value[name], clipped = _clip(row[name]) if preview else (row[name], False)
            field_truncated = clipped or len(value[name]) < row[f"{name}_length"]
            truncated |= field_truncated
            if preview:
                value[f"{name}_truncated"] = field_truncated
        if preview:
            value["content_truncated"] = truncated
        value["artifact"] = ({"id": row["artifact_id"], "base_revision": row["artifact_base_revision"],
                              "patch_sha256": row["artifact_patch_sha256"]} if row["artifact_id"] else None)
        for kind in ("source", "validation"):
            value[f"{kind}_events"] = by_report[row["id"]][kind]
            value[f"{kind}_event_ids"] = [item["event_id"] for item in value[f"{kind}_events"]]
        value["source_changes"] = changes[row["id"]]
        value["superseded_by"] = superseding[row["id"]]
        value["is_superseded"] = bool(value["superseded_by"]["total_count"])
        result.append(value)
    return result


def _exchange_values(connection: sqlite3.Connection, rows: Iterable[sqlite3.Row], *, preview: bool) -> list[dict]:
    rows = list(rows)
    if not rows:
        return []
    reports = _materialize_reports(connection, _report_rows(
        connection, "r.id IN (SELECT value FROM json_each(?))",
        (json.dumps([row["report_id"] for row in rows]),), preview=preview,
    ), preview=preview)
    by_id = {report["id"]: report for report in reports}
    values = []
    for row in rows:
        value = dict(row)
        application_length = value.pop("application_length", len(value["application"]))
        if preview:
            value["application"], clipped = _clip(value["application"])
            value["application_truncated"] = clipped or len(value["application"]) < application_length
            value["content_truncated"] = value["application_truncated"]
        value["source_report"] = by_id[row["report_id"]]
        values.append(value)
    return values


def metadata_for_events(
    connection: sqlite3.Connection, event_ids: Iterable[int], *, preview: bool = True,
) -> dict[int, dict[str, Any]]:
    """Enrich event pages in batches without opening connections or reading source bodies."""
    ids = list(event_ids)
    if not ids:
        return {}
    reports = _materialize_reports(connection, _report_rows(
        connection, "r.event_id IN (SELECT value FROM json_each(?))", (json.dumps(ids),), preview=preview,
    ), preview=preview)
    exchanges = connection.execute(
        "SELECT id,run_id,report_id,target_approach_id,thread_id,comment_id,event_id,author,created_at, "
        "CASE WHEN ? THEN SUBSTR(application,1,512) ELSE application END AS application, "
        "LENGTH(application) AS application_length "
        "FROM report_exchanges WHERE event_id IN (SELECT value FROM json_each(?))", (int(preview), json.dumps(ids)),
    ).fetchall()
    result = {int(report["event_id"]): {"report": report} for report in reports}
    result.update({int(exchange["event_id"]): {"exchange": exchange}
                   for exchange in _exchange_values(connection, exchanges, preview=preview)})
    return result


class ReportStore:
    def __init__(self, forum: Forum, run_id: str):
        self.forum, self.run_id = forum, run_id
        forum.get_run(run_id)
        with forum._connection() as connection:
            if connection.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'approach_reports'").fetchone() is None:
                initialize(connection)

    def _get(self, connection: sqlite3.Connection, report_id: str) -> dict:
        _text(report_id, "report_id", 200, required=True)
        rows = _report_rows(connection, "r.id = ? AND r.run_id = ?", (report_id, self.run_id), preview=False)
        if not rows:
            raise KeyError(f"unknown report in run: {report_id}")
        return _materialize_reports(connection, rows, preview=False)[0]

    def get(self, report_id: str) -> dict:
        with self.forum._connection() as connection:
            connection.execute("BEGIN")
            return self._get(connection, report_id)

    def publish(
        self, approach_id: str, author: str, *, summary: str, conditions: str,
        open_questions: str = "", source_event_ids: Iterable[int], artifact_id: str | None = None,
        validation_event_ids: Iterable[int] = (), supersedes_report_id: str | None = None,
    ) -> dict:
        _text(author, "author", 240, required=True)
        _text(summary, "summary", 8192, required=True)
        _text(conditions, "conditions", 8192, required=True)
        _text(open_questions, "open_questions", 4096)
        sources = _ids(source_event_ids, "source_event_ids", required=True)
        validations = _ids(validation_event_ids, "validation_event_ids")
        with self.forum._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            approach = _approach(connection, self.run_id, approach_id)
            references = connection.execute("""
                SELECT e.id, e.run_id, e.thread_id, cp.relation, cp.artifact_id
                FROM activity e LEFT JOIN comment_provenance cp ON cp.event_id = e.id
                WHERE e.id IN (SELECT value FROM json_each(?))
            """, (json.dumps(sorted(set(sources + validations))),)).fetchall()
            by_id = {int(row["id"]): row for row in references}
            for identifier in sources + validations:
                if identifier not in by_id or by_id[identifier]["run_id"] != self.run_id:
                    raise ValueError("all report references must be existing events in the same run")
            local_sources = [identifier for identifier in sources if by_id[identifier]["thread_id"] == approach["thread_id"]]
            if not local_sources:
                raise ValueError("at least one source event must belong to the approach thread")
            for identifier in validations:
                record = by_id[identifier]
                if record["relation"] != "verifies":
                    raise ValueError("validation events must be existing verifies reports")
                if artifact_id is not None and record["artifact_id"] != artifact_id:
                    raise ValueError("validation event refers to a different artifact")
            if supersedes_report_id is not None:
                previous = self._get(connection, supersedes_report_id)
                if previous["approach_id"] != approach_id or previous["author"].casefold() != author.casefold():
                    raise ValueError("a report can supersede only the same author's report in the same approach")
            report_id = f"report_{uuid.uuid4().hex[:12]}"
            body = (f"Report {report_id}\n\nSummary:\n{summary}\n\nConditions:\n{conditions}\n\n"
                    f"Open questions:\n{open_questions or '(none reported)'}\n\n"
                    f"Source events: {', '.join(map(str, sources))}\n"
                    f"Validation report events: {', '.join(map(str, validations)) or '(none)'}\n"
                    "Validation references are author reports, not automatic certification.")
            if artifact_id is not None:
                body += f"\nArtifact: {artifact_id}"
            if supersedes_report_id is not None:
                body += f"\nSupersedes report: {supersedes_report_id}"
            comment = self.forum._add_comment(
                connection, str(approach["thread_id"]), author, body,
                reply_to_event_id=local_sources[0], evidence_event_ids=sources,
                artifact_id=artifact_id, notification_text="",
            )
            artifact = comment["provenance"].get("artifact") or {}
            connection.execute("""
                INSERT INTO approach_reports(id,run_id,approach_id,thread_id,comment_id,event_id,author,
                    summary,conditions,open_questions,artifact_id,artifact_base_revision,artifact_patch_sha256,
                    supersedes_report_id,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (report_id, self.run_id, approach_id, approach["thread_id"], comment["id"], comment["event_id"],
                  author, summary, conditions, open_questions, artifact_id, artifact.get("base_revision"),
                  artifact.get("patch_sha256"), supersedes_report_id, comment["created_at"]))
            connection.executemany("INSERT INTO report_event_references(report_id,event_id,kind,position) VALUES (?,?,?,?)",
                ((report_id, identifier, kind, position) for kind, identifiers in (("source", sources), ("validation", validations))
                 for position, identifier in enumerate(identifiers)))
            return self._get(connection, report_id)

    def list(
        self, *, approach_id: str | None = None, thread_id: str | None = None,
        query: str = "", limit: int = 30, after: str | None = None,
    ) -> dict:
        self.forum._page_limit(limit)
        after = self.forum._keyset_cursor(after)
        _text(query, "query", 1024)
        with self.forum._connection() as connection:
            connection.execute("BEGIN")
            clauses, parameters = ["r.run_id = ?"], [self.run_id]
            if approach_id is not None:
                _approach(connection, self.run_id, approach_id)
                clauses.append("r.approach_id = ?")
                parameters.append(approach_id)
            if thread_id is not None:
                _text(thread_id, "thread_id", 200, required=True)
                if connection.execute("SELECT 1 FROM threads WHERE id = ? AND run_id = ?", (thread_id, self.run_id)).fetchone() is None:
                    raise ValueError("thread must belong to the same run")
                clauses.append("r.thread_id = ?")
                parameters.append(thread_id)
            if query.strip():
                pattern = "%" + query.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
                clauses.append("(r.summary LIKE ? ESCAPE '\\' OR r.conditions LIKE ? ESCAPE '\\' OR r.open_questions LIKE ? ESCAPE '\\')")
                parameters.extend((pattern, pattern, pattern))
            if after:
                cursor = connection.execute("SELECT event_id FROM approach_reports WHERE id = ? AND run_id = ?", (after, self.run_id)).fetchone()
                if cursor is None:
                    raise KeyError(f"unknown report cursor: {after}")
                clauses.append("r.event_id > ?")
                parameters.append(int(cursor["event_id"]))
            rows = _report_rows(connection, " AND ".join(clauses), (*parameters, limit + 1),
                                preview=True, suffix="ORDER BY r.event_id LIMIT ?")
            items = _materialize_reports(connection, rows[:limit], preview=True)
        has_more = len(rows) > limit
        return {"items": items, "next_cursor": items[-1]["id"] if has_more else None, "has_more": has_more}

    def adopt(self, report_id: str, target_approach_id: str, author: str, *, application: str) -> dict:
        _text(author, "author", 240, required=True)
        _text(application, "application", 8192, required=True)
        with self.forum._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            report = self._get(connection, report_id)
            target = _approach(connection, self.run_id, target_approach_id)
            if report["approach_id"] == target_approach_id:
                raise ValueError("report exchanges require a different target approach")
            existing = connection.execute(
                "SELECT * FROM report_exchanges WHERE report_id = ? AND target_approach_id = ?",
                (report_id, target_approach_id),
            ).fetchone()
            if existing:
                if existing["application"] != application:
                    raise ValueError("report already exchanged into this approach with a different application")
                return _exchange_values(connection, [existing], preview=False)[0]
            exchange_id = f"exchange_{uuid.uuid4().hex[:12]}"
            body = (f"Report exchange {exchange_id}\nSource report: {report_id}\nSource approach: {report['approach_id']}\n\n"
                    f"Proposed application:\n{application}\n\nSource summary:\n{report['summary']}\n\n"
                    f"Source conditions:\n{report['conditions']}\n\nSource open questions:\n{report['open_questions'] or '(none reported)'}\n\n"
                    f"Source events: {', '.join(map(str, report['source_event_ids']))}\n"
                    f"Validation report events: {', '.join(map(str, report['validation_event_ids'])) or '(none)'}\n"
                    "This is the author's proposed application, not an automatic correctness decision.")
            if report["artifact"]:
                body += (f"\nArtifact: {report['artifact']['id']}\nBase revision: {report['artifact']['base_revision']}"
                         f"\nPatch SHA-256: {report['artifact']['patch_sha256']}")
            comment = self.forum._add_comment(
                connection, str(target["thread_id"]), author, body, reply_to_event_id=report["event_id"],
                relation="supports", artifact_id=report["artifact_id"],
                evidence_event_ids=report["source_event_ids"], notification_text="",
            )
            connection.execute("""
                INSERT INTO report_exchanges(id,run_id,report_id,target_approach_id,thread_id,comment_id,event_id,
                    author,application,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)
            """, (exchange_id, self.run_id, report_id, target_approach_id, target["thread_id"], comment["id"],
                  comment["event_id"], author, application, comment["created_at"]))
            row = connection.execute("SELECT * FROM report_exchanges WHERE id = ?", (exchange_id,)).fetchone()
            return _exchange_values(connection, [row], preview=False)[0]
