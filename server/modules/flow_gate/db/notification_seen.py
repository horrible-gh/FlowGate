"""CRUD for the notification_seen table (R0001 group 0045, NR0003 — option A, the 🔔 notification centre).

One row per (user, project): the watermark up to which the user has read the document-inflow feed.
The feed itself comes from workflow_events (see dashboard_service.get_notification_feed); this table
only stores how far the user has read so the 🔔 badge can show an "unread N" count that survives
reloads/tabs/devices. "Mark all read" overwrites last_seen_at via UPSERT — mirrors the 0015
document_mention_copies user-state pattern (db/mention_copies.py).
"""
from __future__ import annotations

from typing import Optional

from .connection import get_store, now_iso


def get_last_seen(user_id: str, project_id: str) -> Optional[str]:
    """Return the user's last-seen watermark for a project, or None if never marked read."""
    row = get_store()._fetch_one(
        "SELECT last_seen_at FROM notification_seen WHERE user_id = ? AND project_id = ?",
        [user_id, project_id],
    )
    return row["last_seen_at"] if row else None


def mark_seen(user_id: str, project_id: str, seen_at: Optional[str] = None) -> str:
    """Record (or overwrite) the user's last-seen watermark for a project; return it."""
    now = seen_at or now_iso()
    get_store()._execute(
        "INSERT INTO notification_seen (user_id, project_id, last_seen_at) "
        "VALUES (?, ?, ?) "
        "ON CONFLICT(user_id, project_id) DO UPDATE SET last_seen_at = excluded.last_seen_at",
        [user_id, project_id, now],
    )
    return now
