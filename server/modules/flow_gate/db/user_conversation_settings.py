"""Each user's settings of one chat (flowgate.v02test.0025, migration 185).

    provider_id             the AI picked in this chat. NULL (or no row) = the user's default AI.
                            A soft-deleted AI reads as NULL (the JOIN below), so a removed AI is
                            never offered back as this chat's pick.
    context_checkpoint_seq  the seq of the message the AI context starts from. NULL = no checkpoint
                            (the user's context mode / turn count alone decides the window).

The two columns are saved independently: a PATCH of one never touches the other.
"""
from __future__ import annotations

from typing import Optional

from .connection import get_store, now_iso

COLUMNS = ("provider_id", "context_checkpoint_seq")


def get(user_id: str, doc_id: str) -> Optional[dict]:
    """The stored row as {provider_id, context_checkpoint_seq, updated_at}, or None."""
    row = get_store()._fetch_one(
        "SELECT s.provider_id, s.context_checkpoint_seq, s.updated_at, p.deleted_at AS provider_deleted_at "
        "FROM user_conversation_settings s LEFT JOIN ai_providers p ON p.provider_id = s.provider_id "
        "WHERE s.user_id = ? AND s.doc_id = ?",
        [user_id, doc_id],
    )
    if row is None:
        return None
    provider_id = row.get("provider_id")
    if provider_id and row.get("provider_deleted_at"):
        provider_id = None
    seq = row.get("context_checkpoint_seq")
    return {
        "provider_id": provider_id or None,
        "context_checkpoint_seq": int(seq) if seq is not None else None,
        "updated_at": row.get("updated_at"),
    }


def upsert(user_id: str, doc_id: str, values: dict) -> None:
    """Write only the columns present in ``values`` (a subset of COLUMNS)."""
    columns = [c for c in COLUMNS if c in values]
    if not columns:
        return
    now = now_iso()
    names = ", ".join(columns)
    marks = ", ".join("?" for _ in columns)
    updates = ", ".join(f"{c} = excluded.{c}" for c in columns)
    get_store()._execute(
        f"INSERT INTO user_conversation_settings (user_id, doc_id, {names}, updated_at) "
        f"VALUES (?, ?, {marks}, ?) "
        f"ON CONFLICT(user_id, doc_id) DO UPDATE SET {updates}, updated_at = excluded.updated_at",
        [user_id, doc_id, *[values[c] for c in columns], now],
    )
