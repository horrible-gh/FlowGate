"""CRUD for the document_mention_copies table (R0001 group 0015, NR0003 rev4 — option B).

One row per (user, doc): the last mention block the user copied to hand the document off to an
AI worker, so the document header can persistently show "<mention name> · copied HH:MM" across reloads,
tabs, and devices. A new copy overwrites the previous via UPSERT — the badge shows only the most
recent (NR0003 §1/§3). The label is derived on the client from mention_kind; the server stores the
stable code only, keeping the badge locale-correct.

Follows the get_store() + inline SQL pattern of the sibling child-record tables (document_reviews.py).
"""
from __future__ import annotations

from typing import Optional

from .connection import get_store, now_iso

_COLS = "user_id, doc_id, mention_kind, copied_at"


def upsert(user_id: str, doc_id: str, mention_kind: str) -> dict:
    """Record (or overwrite) the user's last copied mention for a document; return the row."""
    store = get_store()
    now = now_iso()
    store._execute(
        "INSERT INTO document_mention_copies (user_id, doc_id, mention_kind, copied_at) "
        "VALUES (?, ?, ?, ?) "
        "ON CONFLICT(user_id, doc_id) DO UPDATE SET "
        "mention_kind = excluded.mention_kind, copied_at = excluded.copied_at",
        [user_id, doc_id, mention_kind, now],
    )
    row = store._fetch_one(
        f"SELECT {_COLS} FROM document_mention_copies WHERE user_id = ? AND doc_id = ?",
        [user_id, doc_id],
    )
    return row  # type: ignore[return-value]


def get(user_id: str, doc_id: str) -> Optional[dict]:
    """Return the user's last copied-mention state for a document, or None if never copied."""
    return get_store()._fetch_one(
        f"SELECT {_COLS} FROM document_mention_copies WHERE user_id = ? AND doc_id = ?",
        [user_id, doc_id],
    )
