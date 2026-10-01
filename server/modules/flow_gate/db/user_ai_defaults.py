"""Each user's default AI (flowgate.v02.0017 CH0006 (3), migration 134).

    provider_id  the user's default AI (a system AI or one of their own). No row, or NULL after
                 the AI was deleted, means "follow the system default".
"""
from __future__ import annotations

from typing import Optional

from .connection import get_store, now_iso


def get(user_id: str) -> Optional[str]:
    row = get_store()._fetch_one(
        "SELECT provider_id FROM user_ai_defaults WHERE user_id = ?", [user_id],
    )
    return (row or {}).get("provider_id") or None


def save(user_id: str, provider_id: Optional[str]) -> None:
    """Set the user's default; None deletes the row (follow the system default)."""
    store = get_store()
    if provider_id is None:
        store._execute("DELETE FROM user_ai_defaults WHERE user_id = ?", [user_id])
        return
    store._execute(
        "INSERT INTO user_ai_defaults (user_id, provider_id, updated_at) VALUES (?, ?, ?) "
        "ON CONFLICT(user_id) DO UPDATE SET "
        "provider_id = excluded.provider_id, updated_at = excluded.updated_at",
        [user_id, provider_id, now_iso()],
    )
