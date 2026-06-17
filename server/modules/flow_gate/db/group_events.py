"""CRUD for the `group_events` table (migration 048).

Group-level terminal events (group_disposed / group_closed) belong to a GROUP, not a
document. They used to be written into the document-scoped `events` table with
doc_id = group_id, which violates `events.doc_id REFERENCES documents(doc_id)` once FK
enforcement is on (B0001 / NR0003). This dedicated table references `groups` instead, so
both the dispose and close paths record their event without a documents FK.
"""
from __future__ import annotations
from datetime import datetime
from .connection import get_store


def insert_group_event(
    group_id: str, event_type: str, reason: str = None, note: str = None,
) -> int:
    """Record a group-level event and return the generated event_id (lastrowid)."""
    now = datetime.now().isoformat()
    store = get_store()
    with store.transaction() as s:
        s._execute(
            "INSERT INTO group_events (group_id, event_type, reason, note, created_at)"
            " VALUES (?, ?, ?, ?, ?)",
            [group_id, event_type, reason, note, now],
        )
        row = s._fetch_one("SELECT last_insert_rowid() AS rid")
    return row["rid"] if row else None


def get_group_events(group_id: str) -> list[dict]:
    """Return group-level events for a group, newest first."""
    return get_store()._fetch_all(
        "SELECT * FROM group_events WHERE group_id = ? ORDER BY event_id DESC",
        [group_id],
    )
