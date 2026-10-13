"""Card memo store (flowgate.v02.0056 T#3).

Migration 149_graph_node_memos.sql. One row per memo, keyed by the card like its settings (``graph_node_settings``):
the Flow and the node key, so a card keeps its memos whether it is only in the draft or in confirmed revisions (a
removed node's key is retired and never reused, and the Flow's rows go with the Flow). ``ai_ref`` is 0 / 1.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Iterable, Optional

from modules.flow_gate.db.connection import get_store

MEMO_TABLE = "graph_node_memos"
_COLUMNS = "memo_id, group_id, node_key, title, body, ai_ref, created_by, created_at, updated_at, updated_by"


def _now() -> str:
    """ISO 8601 UTC with milliseconds, like the other Graph rows (P0008 §1.1)."""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _shape(row: dict) -> dict:
    out = dict(row)
    out["ai_ref"] = bool(out.get("ai_ref"))
    out["body"] = out.get("body") or ""
    return out


def available() -> bool:
    """False until migration 149 is applied: no card has memos (and no AI reads any)."""
    return get_store().table_exists(MEMO_TABLE)


def memos(flow_id: str, node_keys: Optional[Iterable[str]] = None, *, ai_ref_only: bool = False) -> list[dict]:
    """The memos of the Flow's cards (optionally of some nodes, or only those the AI reads), oldest first."""
    if not available():
        return []
    sql = f"SELECT {_COLUMNS} FROM graph_node_memos WHERE group_id = ?"
    params: list = [flow_id]
    if ai_ref_only:
        sql += " AND ai_ref = 1"
    found = get_store()._fetch_all(sql + " ORDER BY created_at, memo_id", params)
    out = [_shape(r) for r in found or []]
    if node_keys is not None:
        keys = set(node_keys)
        out = [m for m in out if m["node_key"] in keys]
    return out


def memo(flow_id: str, node_key: str, memo_id: str) -> Optional[dict]:
    row = get_store()._fetch_one(
        f"SELECT {_COLUMNS} FROM graph_node_memos WHERE memo_id = ? AND group_id = ? AND node_key = ?",
        [memo_id, flow_id, node_key])
    return _shape(row) if row else None


def insert(flow_id: str, node_key: str, title: str, body: str, ai_ref: bool, by: Optional[str]) -> str:
    memo_id = f"mem_{uuid.uuid4().hex}"
    now = _now()
    get_store()._execute(
        f"INSERT INTO graph_node_memos ({_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [memo_id, flow_id, node_key, title, body, 1 if ai_ref else 0, by, now, now, by],
    )
    return memo_id


def update(flow_id: str, node_key: str, memo_id: str, fields: dict, by: Optional[str]) -> int:
    """Change some of ``title`` / ``body`` / ``ai_ref``; returns the rows changed."""
    sets, params = [], []
    for name in ("title", "body", "ai_ref"):
        if name in fields:
            sets.append(f"{name} = ?")
            params.append((1 if fields[name] else 0) if name == "ai_ref" else fields[name])
    sets += ["updated_at = ?", "updated_by = ?"]
    params += [_now(), by, memo_id, flow_id, node_key]
    return get_store()._execute_affected(
        f"UPDATE graph_node_memos SET {', '.join(sets)} WHERE memo_id = ? AND group_id = ? AND node_key = ?", params)


def delete(flow_id: str, node_key: str, memo_id: str) -> int:
    return get_store()._execute_affected(
        "DELETE FROM graph_node_memos WHERE memo_id = ? AND group_id = ? AND node_key = ?",
        [memo_id, flow_id, node_key])
