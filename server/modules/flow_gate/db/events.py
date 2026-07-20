"""CRUD for the legacy `events` table (migrated from store.py, phase 'event').

This legacy table differs from the newer `workflow_events` table in db/workflow_events.py.
It preserves the `events` table used by process_service/service through db.insert_event and similar calls.
The store.FlowGateStore event methods were ported with identical SQL and return shapes.
"""
from __future__ import annotations
from datetime import datetime
from typing import Optional, Any
from .connection import get_store


def insert_event(
    doc_id: str, event_type: str, memo_file: str = None,
    file_hash: str = None, reason: str = None,
    related_doc_id: str = None, related_target_id: str = None, note: str = None,
) -> int:
    """Record an event and return the generated event_id (lastrowid)."""
    now = datetime.now().isoformat()
    store = get_store()
    with store.transaction() as s:
        s._execute(
            "INSERT INTO events"
            " (doc_id, event_type, memo_file, file_hash, reason,"
            "  related_doc_id, related_target_id, note, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [doc_id, event_type, memo_file, file_hash, reason,
             related_doc_id, related_target_id, note, now],
        )
        row = s._fetch_one("SELECT last_insert_rowid() AS rid")
    return row["rid"] if row else None


def get_created_memo_file(doc_id: str) -> Optional[str]:
    """Return the memo_file from the creation event."""
    row = get_store()._fetch_one(
        "SELECT memo_file FROM events WHERE doc_id = ? AND event_type = 'created'"
        " AND memo_file IS NOT NULL AND memo_file != ''"
        " ORDER BY event_id DESC LIMIT 1",
        [doc_id],
    )
    return row["memo_file"] if row else None


def get_created_memo_files_map_by_project(project_id: str) -> dict[str, str]:
    """Return a doc_id -> memo_file map from the creation events of one project.

    Batch counterpart of get_created_memo_file() (0275 NR0003 원인 3: the group
    tree issued this lookup once per document). 0276 NR0003 발견 1: the caller
    passed the project's entire doc_id list, so the chunked
    `doc_id IN (?×900)` loop spent thousands of bind parameters restating what
    `d.project_id = ?` says with one. Narrowing through documents keeps the row
    selection identical — the latest 'created' event per document that carries a
    non-empty memo_file — while the query count drops to exactly one.
    """
    result: dict[str, str] = {}
    if not project_id:
        return result
    rows = get_store()._fetch_all(
        "SELECT e.doc_id, e.memo_file"
        " FROM events e"
        " INNER JOIN ("
        "     SELECT e2.doc_id, MAX(e2.event_id) AS max_event_id"
        "     FROM events e2"
        "     INNER JOIN documents d ON d.doc_id = e2.doc_id"
        "     WHERE e2.event_type = 'created'"
        "       AND e2.memo_file IS NOT NULL AND e2.memo_file != ''"
        "       AND d.project_id = ?"
        "     GROUP BY e2.doc_id"
        " ) latest ON e.doc_id = latest.doc_id AND e.event_id = latest.max_event_id",
        [project_id],
    )
    for r in rows:
        result[r["doc_id"]] = r["memo_file"]
    return result


def is_file_processed(memo_file: str) -> bool:
    """Return whether the file has been processed."""
    row = get_store()._fetch_one(
        "SELECT 1 AS ok FROM events WHERE memo_file = ? AND event_type = 'created' LIMIT 1",
        [memo_file],
    )
    return row is not None


def is_hash_processed(file_hash: str) -> bool:
    """Return whether the hash has been processed."""
    row = get_store()._fetch_one(
        "SELECT 1 AS ok FROM events WHERE file_hash = ? AND event_type = 'created' LIMIT 1",
        [file_hash],
    )
    return row is not None


def get_events_by_doc_id(doc_id: str) -> list[dict]:
    """Return events for a document, newest first."""
    return get_store()._fetch_all(
        "SELECT * FROM events WHERE doc_id = ? ORDER BY event_id DESC", [doc_id]
    )


def get_recent_events_by_doc_id(doc_id: str, limit: int = 5) -> list[dict]:
    """Return recent events for a document."""
    return get_store()._fetch_all(
        "SELECT * FROM events WHERE doc_id = ? ORDER BY event_id DESC LIMIT ?",
        [doc_id, limit],
    )


def get_recent_events(limit: int = 5) -> list[dict]:
    """Return recent events."""
    return get_store()._fetch_all(
        "SELECT * FROM events ORDER BY event_id DESC LIMIT ?", [limit]
    )


def get_latest_events_map(doc_ids: list[str]) -> dict[str, dict]:
    """Return a map of the latest event for each document.

    Chunked to stay under SQLite's historical 999 bind-variable limit
    (0276 NR0003 발견 1: this was the one IN(...) batch left unchunked, so a
    caller with 1000+ open documents raised "too many SQL variables" instead of
    returning rows).
    """
    result: dict[str, dict] = {}
    if not doc_ids:
        return result
    store = get_store()
    ids = list(doc_ids)
    chunk_size = 900
    for i in range(0, len(ids), chunk_size):
        chunk = ids[i:i + chunk_size]
        placeholders = ",".join(["?"] * len(chunk))
        rows = store._fetch_all(
            f"SELECT e.doc_id, e.event_type, e.note, e.memo_file, e.created_at"
            f" FROM events e"
            f" INNER JOIN ("
            f"     SELECT doc_id, MAX(event_id) AS max_event_id"
            f"     FROM events WHERE doc_id IN ({placeholders})"
            f"     GROUP BY doc_id"
            f" ) latest ON e.doc_id = latest.doc_id AND e.event_id = latest.max_event_id",
            chunk,
        )
        for r in rows:
            result[r["doc_id"]] = dict(r)
    return result


def get_conflict_events(limit: int = 50) -> list[dict]:
    """Return conflict events."""
    return get_store()._fetch_all(
        "SELECT * FROM events WHERE event_type = 'conflict_detected'"
        " ORDER BY event_id DESC LIMIT ?",
        [limit],
    )
