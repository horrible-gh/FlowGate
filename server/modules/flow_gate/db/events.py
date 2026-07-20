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


def get_created_memo_files_map(doc_ids: list[str]) -> dict[str, str]:
    """Return a doc_id -> memo_file map from the creation events.

    Batch counterpart of get_created_memo_file() (0275 NR0003 원인 3: the group
    tree issued this lookup once per document). Same row selection: the latest
    'created' event per document that carries a non-empty memo_file.
    Chunked to stay under SQLite's historical 999 bind-variable limit.
    """
    result: dict[str, str] = {}
    if not doc_ids:
        return result
    store = get_store()
    ids = list(doc_ids)
    chunk_size = 900
    for i in range(0, len(ids), chunk_size):
        chunk = ids[i:i + chunk_size]
        placeholders = ",".join(["?"] * len(chunk))
        rows = store._fetch_all(
            f"SELECT e.doc_id, e.memo_file"
            f" FROM events e"
            f" INNER JOIN ("
            f"     SELECT doc_id, MAX(event_id) AS max_event_id"
            f"     FROM events"
            f"     WHERE event_type = 'created'"
            f"       AND memo_file IS NOT NULL AND memo_file != ''"
            f"       AND doc_id IN ({placeholders})"
            f"     GROUP BY doc_id"
            f" ) latest ON e.doc_id = latest.doc_id AND e.event_id = latest.max_event_id",
            chunk,
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
    """Return a map of the latest event for each document."""
    if not doc_ids:
        return {}
    placeholders = ",".join(["?"] * len(doc_ids))
    rows = get_store()._fetch_all(
        f"SELECT e.doc_id, e.event_type, e.note, e.memo_file, e.created_at"
        f" FROM events e"
        f" INNER JOIN ("
        f"     SELECT doc_id, MAX(event_id) AS max_event_id"
        f"     FROM events WHERE doc_id IN ({placeholders})"
        f"     GROUP BY doc_id"
        f" ) latest ON e.doc_id = latest.doc_id AND e.event_id = latest.max_event_id",
        list(doc_ids),
    )
    return {r["doc_id"]: dict(r) for r in rows}


def get_conflict_events(limit: int = 50) -> list[dict]:
    """Return conflict events."""
    return get_store()._fetch_all(
        "SELECT * FROM events WHERE event_type = 'conflict_detected'"
        " ORDER BY event_id DESC LIMIT ?",
        [limit],
    )
