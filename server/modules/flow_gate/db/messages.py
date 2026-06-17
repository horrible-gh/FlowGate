"""project_messages CRUD — follows the sqloader.load pattern (DB0008 §7).

Inline SQL is prohibited. Only use SQL registered in queries.json (`messages` namespace).
Mirrors questions.py/answers.py: store._sql("messages.<key>") + _fetch_*/_execute + now_iso().
"""
from __future__ import annotations

from typing import Optional

from .connection import get_store, now_iso


def list_by_project(project: str) -> list[dict]:
    """All messages for the management screen (P0006 §4.1)."""
    store = get_store()
    return store._fetch_all(store._sql("messages.list_messages_by_project"), [project])


def list_for_dialog(project: str, doc_type: str) -> list[dict]:
    """Requested type + [전체]('*') union for the mention-add dialog (P0006 §4.5).

    Returns the wire set as-is; display order/priority/dedup is L0007's concern.
    """
    store = get_store()
    return store._fetch_all(
        store._sql("messages.get_messages_for_dialog"), [project, doc_type]
    )


def get_by_id(message_id: int) -> Optional[dict]:
    """Single row by PK, or None (router maps None -> 404)."""
    store = get_store()
    return store._fetch_one(store._sql("messages.get_message_by_id"), [message_id])


def create(project: str, doc_type: str, message: str) -> dict:
    """Insert a message and return the created row (P0006 §4.2).

    INSERT + last_insert_rowid() recovery must share one connection, so they are wrapped
    in store.transaction() (DB0008 §7; events.insert_event precedent). Outside a
    transaction every _execute opens a fresh connection, which would misread the rowid.
    """
    store = get_store()
    now = now_iso()
    with store.transaction() as s:
        s._execute(store._sql("messages.insert_message"), [project, doc_type, message, now])
        row = s._fetch_one("SELECT last_insert_rowid() AS rid")
        new_id = row["rid"] if row else None
        return s._fetch_one(store._sql("messages.get_message_by_id"), [new_id])


def update(message_id: int, updates: dict) -> Optional[dict]:
    """Read-modify-write the row, return the updated row, or None if absent (P0006 §4.3).

    `updates` may carry a subset of {doc_type, message}; unspecified fields keep their value.
    """
    store = get_store()
    current = get_by_id(message_id)
    if current is None:
        return None
    doc_type = updates.get("doc_type", current["doc_type"])
    message = updates.get("message", current["message"])
    store._execute(
        store._sql("messages.update_message"), [doc_type, message, now_iso(), message_id]
    )
    return get_by_id(message_id)


def delete(message_id: int) -> bool:
    """Delete by PK. Returns True when the row existed (router maps False -> 404)."""
    store = get_store()
    if get_by_id(message_id) is None:
        return False
    store._execute(store._sql("messages.delete_message"), [message_id])
    return True
