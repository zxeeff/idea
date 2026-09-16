"""Append-only, author-reported links between forum events.

Relations describe what a contributor says a comment does. They never certify
an artifact, establish consensus, or replace the original event.
"""

from __future__ import annotations

import json
import sqlite3
from itertools import islice
from typing import Any, Iterable


RELATIONS = frozenset({"reply", "supports", "challenges", "verifies", "retracts", "supersedes"})
MAX_EVENT_ID = 9_223_372_036_854_775_807
MAX_EVIDENCE_EVENTS = 16
MAX_VALIDATION_BYTES = 32 * 1024


def initialize(connection: sqlite3.Connection) -> None:
    # Keep the original five-column comments layout: older producers still use
    # positional INSERTs. No history or notification cursor is rewritten.
    connection.executescript("""
        CREATE TABLE IF NOT EXISTS comment_provenance (
            comment_id TEXT PRIMARY KEY REFERENCES comments(id),
            event_id INTEGER NOT NULL UNIQUE REFERENCES activity(id),
            run_id TEXT NOT NULL REFERENCES runs(id),
            reply_to_event_id INTEGER REFERENCES activity(id),
            relation TEXT NOT NULL CHECK (relation IN
                ('reply', 'supports', 'challenges', 'verifies', 'retracts', 'supersedes')),
            artifact_id TEXT,
            artifact_base_revision TEXT,
            artifact_patch_sha256 TEXT,
            validation TEXT NOT NULL,
            CHECK (reply_to_event_id IS NULL OR reply_to_event_id < event_id)
        );
        CREATE INDEX IF NOT EXISTS provenance_by_parent
            ON comment_provenance(reply_to_event_id, event_id);
        CREATE TABLE IF NOT EXISTS comment_evidence (
            comment_id TEXT NOT NULL REFERENCES comment_provenance(comment_id),
            event_id INTEGER NOT NULL REFERENCES activity(id),
            position INTEGER NOT NULL,
            PRIMARY KEY(comment_id, event_id)
        );
        CREATE INDEX IF NOT EXISTS evidence_by_event
            ON comment_evidence(event_id, comment_id);
        CREATE INDEX IF NOT EXISTS activity_by_subject
            ON activity(kind, subject_id, id);
    """)


def event_id(value: Any, name: str, *, allow_zero: bool = False) -> int:
    minimum = 0 if allow_zero else 1
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= MAX_EVENT_ID:
        raise ValueError(f"{name} must be a {'non-negative' if allow_zero else 'positive'} 64-bit event ID")
    return value


def validate(
    connection: sqlite3.Connection, run_id: str, author: str, *,
    reply_to_event_id: int | None, relation: str, artifact_id: str | None,
    validation: str, evidence_event_ids: Iterable[int],
) -> dict[str, Any] | None:
    if not isinstance(relation, str) or relation not in RELATIONS:
        raise ValueError("unknown comment relation")
    if reply_to_event_id is not None:
        event_id(reply_to_event_id, "reply_to_event_id")
    elif relation != "reply":
        raise ValueError("this relation requires reply_to_event_id")
    if not isinstance(validation, str) or len(validation.encode("utf-8")) > MAX_VALIDATION_BYTES:
        raise ValueError("validation must be text of at most 32 KiB")
    if relation == "verifies" and not validation.strip():
        raise ValueError("verifies requires reported validation details")
    if artifact_id is not None and (
        not isinstance(artifact_id, str) or not artifact_id or len(artifact_id) > 200
    ):
        raise ValueError("artifact_id must be a nonempty artifact ID")
    if isinstance(evidence_event_ids, (str, bytes, dict)):
        raise ValueError("evidence_event_ids must be an iterable of event IDs")
    try:
        evidence = list(islice(iter(evidence_event_ids), MAX_EVIDENCE_EVENTS + 1))
    except TypeError as exc:
        raise ValueError("evidence_event_ids must be an iterable of event IDs") from exc
    if len(evidence) > MAX_EVIDENCE_EVENTS:
        raise ValueError("at most 16 evidence events are allowed")
    evidence = list(dict.fromkeys(event_id(value, "evidence event") for value in evidence))
    references = set(evidence)
    if reply_to_event_id is not None:
        references.add(reply_to_event_id)
    # All references must already exist before the new event is inserted. Under
    # the caller's write transaction this also makes provenance cycles impossible.
    rows = connection.execute(
        "SELECT id, run_id, author FROM activity WHERE id IN (SELECT value FROM json_each(?))",
        (json.dumps(sorted(references)),),
    ).fetchall() if references else []
    existing = {int(row["id"]): row for row in rows}
    for reference in references:
        if reference not in existing:
            raise ValueError(f"unknown referenced event: {reference}")
        if existing[reference]["run_id"] != run_id:
            raise ValueError("referenced event belongs to another run")
    if relation in {"retracts", "supersedes"} and (
        str(existing[reply_to_event_id]["author"]).casefold() != author.casefold()
    ):
        raise ValueError("only the original author can retract or supersede an event")
    artifact = None
    if artifact_id is not None:
        table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'workspace_artifacts'"
        ).fetchone()
        artifact = connection.execute(
            "SELECT id, run_id, base_revision, patch_sha256 FROM workspace_artifacts WHERE id = ?",
            (artifact_id,),
        ).fetchone() if table else None
        if artifact is None:
            raise ValueError(f"unknown artifact: {artifact_id}")
        if artifact["run_id"] != run_id:
            raise ValueError("artifact belongs to another run")
    if not references and artifact is None and not validation:
        return None
    return {
        "reply_to_event_id": reply_to_event_id, "relation": relation,
        "artifact_id": artifact_id, "validation": validation,
        "evidence_event_ids": evidence,
        "artifact_base_revision": artifact["base_revision"] if artifact else None,
        "artifact_patch_sha256": artifact["patch_sha256"] if artifact else None,
    }


def record(
    connection: sqlite3.Connection, comment_id: str, new_event_id: int,
    run_id: str, value: dict[str, Any] | None,
) -> None:
    if value is None:
        return
    connection.execute("""
        INSERT INTO comment_provenance(
            comment_id, event_id, run_id, reply_to_event_id, relation, artifact_id,
            artifact_base_revision, artifact_patch_sha256, validation
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (comment_id, new_event_id, run_id, value["reply_to_event_id"], value["relation"],
          value["artifact_id"], value["artifact_base_revision"], value["artifact_patch_sha256"],
          value["validation"]))
    connection.executemany(
        "INSERT INTO comment_evidence(comment_id, event_id, position) VALUES (?, ?, ?)",
        ((comment_id, reference, position) for position, reference in enumerate(value["evidence_event_ids"])),
    )


def _event_link(row: sqlite3.Row, *, preview: bool) -> dict[str, Any]:
    author = str(row["author"])
    return {"event_id": int(row["id"]), "thread_id": row["thread_id"],
            "subject_id": row["subject_id"], "kind": row["kind"],
            "author": author[:240] if preview else author}


def for_events(
    connection: sqlite3.Connection, event_ids: Iterable[int], *, preview: bool = False,
) -> dict[int, dict[str, Any]]:
    """Batch provenance and navigation references; never fetch referenced bodies."""
    ids = list(event_ids)
    if not ids:
        return {}
    rows = connection.execute("""
        SELECT comment_id, event_id, reply_to_event_id, relation, artifact_id,
               artifact_base_revision, artifact_patch_sha256,
               CASE WHEN ? THEN SUBSTR(validation, 1, 512) ELSE validation END AS validation,
               LENGTH(validation) > 512 AS validation_clipped
        FROM comment_provenance WHERE event_id IN (SELECT value FROM json_each(?))
    """, (int(preview), json.dumps(ids))).fetchall()
    if not rows:
        return {}
    evidence_rows = connection.execute("""
        SELECT ce.comment_id, ce.event_id FROM comment_evidence ce
        JOIN comment_provenance cp ON cp.comment_id = ce.comment_id
        WHERE cp.event_id IN (SELECT value FROM json_each(?))
        ORDER BY ce.comment_id, ce.position
    """, (json.dumps(ids),)).fetchall()
    references = {int(row["reply_to_event_id"]) for row in rows if row["reply_to_event_id"] is not None}
    evidence: dict[str, list[int]] = {}
    for row in evidence_rows:
        reference = int(row["event_id"])
        references.add(reference)
        evidence.setdefault(row["comment_id"], []).append(reference)
    links = {int(row["id"]): _event_link(row, preview=preview) for row in connection.execute(
        "SELECT id, thread_id, subject_id, kind, author FROM activity WHERE id IN (SELECT value FROM json_each(?))",
        (json.dumps(sorted(references)),),
    ).fetchall()} if references else {}
    result = {}
    for row in rows:
        target = row["reply_to_event_id"]
        evidence_ids = evidence.get(row["comment_id"], [])
        value = {"reply_to_event_id": target, "relation": row["relation"],
                 "artifact_id": row["artifact_id"], "validation": row["validation"],
                 "evidence_event_ids": evidence_ids,
                 "reply_to_event": links.get(target),
                 "evidence_events": [links[reference] for reference in evidence_ids]}
        if row["artifact_id"] is not None:
            value["artifact"] = {"id": row["artifact_id"], "base_revision": row["artifact_base_revision"],
                                 "patch_sha256": row["artifact_patch_sha256"]}
        if preview:
            value["validation_truncated"] = bool(row["validation_clipped"])
        result[int(row["event_id"])] = value
    return result


def enrich_comments(
    connection: sqlite3.Connection, comments: Iterable[sqlite3.Row | dict[str, Any]],
) -> list[dict[str, Any]]:
    items = [dict(comment) for comment in comments]
    if not items:
        return items
    event_ids = {row["subject_id"]: int(row["id"]) for row in connection.execute("""
        SELECT subject_id, MIN(id) AS id FROM activity
        WHERE kind = 'comment' AND subject_id IN (SELECT value FROM json_each(?))
        GROUP BY subject_id
    """, (json.dumps([item["id"] for item in items]),)).fetchall()}
    metadata = for_events(connection, event_ids.values())
    for item in items:
        item["event_id"] = event_ids.get(item["id"])
        if item["event_id"] in metadata:
            item["provenance"] = metadata[item["event_id"]]
    return items
