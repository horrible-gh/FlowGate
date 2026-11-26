"""Remarks on the cases of a test scenario (F81)."""
from __future__ import annotations

import uuid
from typing import Optional

from .connection import get_store, now_iso

_TABLE = "test_case_remarks"


def available() -> bool:
    return get_store().table_exists(_TABLE)


def list_by_doc(doc_id: str, case_id: Optional[str] = None) -> list[dict]:
    """Oldest first. Empty when the table is not there yet (a database before migration 214)."""
    if not doc_id or not available():
        return []
    sql, params = f"SELECT * FROM {_TABLE} WHERE doc_id = ?", [doc_id]
    if case_id:
        sql += " AND case_id = ?"
        params.append(case_id)
    return get_store()._fetch_all(sql + " ORDER BY created_at ASC, remark_id ASC", params)


def get(remark_id: str) -> Optional[dict]:
    if not remark_id or not available():
        return None
    return get_store()._fetch_one(f"SELECT * FROM {_TABLE} WHERE remark_id = ?", [remark_id])


def insert(*, doc_id: str, case_id: str, author_kind: str, author_name: str, phase: str,
           body: str) -> dict:
    remark_id = "tcr_" + uuid.uuid4().hex
    now = now_iso()
    get_store()._execute(
        f"INSERT INTO {_TABLE} (remark_id, doc_id, case_id, author_kind, author_name, phase, "
        "state, body, ai_reply, created_at, updated_at, resolved_at) "
        "VALUES (?, ?, ?, ?, ?, ?, 'open', ?, NULL, ?, ?, NULL)",
        [remark_id, doc_id, case_id, author_kind, author_name, phase, body, now, now],
    )
    return get(remark_id) or {}


def update(remark_id: str, *, state: Optional[str] = None,
           ai_reply: Optional[str] = None) -> Optional[dict]:
    """Change the state and / or the AI's answer; fields left ``None`` keep their value."""
    sets, params = [], []
    now = now_iso()
    if state is not None:
        sets += ["state = ?", "resolved_at = ?"]
        params += [state, now if state == "resolved" else None]
    if ai_reply is not None:
        sets.append("ai_reply = ?")
        params.append(ai_reply)
    if sets:
        get_store()._execute(
            f"UPDATE {_TABLE} SET {', '.join(sets)}, updated_at = ? WHERE remark_id = ?",
            [*params, now, remark_id],
        )
    return get(remark_id)
