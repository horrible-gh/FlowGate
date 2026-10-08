"""Layout store: Flow guard row, node positions, viewports, identity mapping, retired keys.

DB0010 T01~T06 (migration 141_graph_layout_identity.sql), queries §4.2 Q-W1~Q-W6 / Q-R1~Q-R4.
SQL is written in the SQLite/PostgreSQL form; ``db/dialect.py`` rewrites it for MySQL.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable, Optional

from modules.flow_gate.db.connection import get_store

RETIRE_REASONS = ("rematch", "supersede", "root_binding", "draft_removed", "revision_removed")


def utc_now_ms() -> str:
    """ISO 8601 UTC with milliseconds (P0008 §1.1, DB0010 §2.0)."""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


# ── Flow guard (T01) ─────────────────────────────────────────────────────────

def ensure_guard_row(flow_id: str) -> None:
    """Q-W1. Raises the driver's integrity error when the Flow row is missing (FK)."""
    get_store()._execute(
        "INSERT INTO graph_flow_guards (group_id, guard_seq, created_at) VALUES (?, 0, ?) "
        "ON CONFLICT (group_id) DO NOTHING",
        [flow_id, utc_now_ms()],
    )


def bump_guard(flow_id: str) -> int:
    """Q-W2. Must be the first statement of the guarded transaction (L0009 §2.1.3)."""
    return get_store()._execute_affected(
        "UPDATE graph_flow_guards SET guard_seq = guard_seq + 1 WHERE group_id = ?", [flow_id]
    )


# ── Positions (T02) / viewports (T03) ────────────────────────────────────────

def positions(flow_id: str) -> dict[str, tuple[float, float]]:
    """Q-R1."""
    rows = get_store()._fetch_all(
        "SELECT node_key, x, y FROM graph_node_positions WHERE group_id = ?", [flow_id]
    )
    return {r["node_key"]: (float(r["x"]), float(r["y"])) for r in rows or []}


def save_position(flow_id: str, node_key: str, x: float, y: float, by: Optional[str]) -> None:
    """Q-W3 — last write wins."""
    get_store()._execute(
        "INSERT INTO graph_node_positions (group_id, node_key, x, y, updated_at, updated_by) "
        "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (group_id, node_key) DO UPDATE SET "
        "x = excluded.x, y = excluded.y, updated_at = excluded.updated_at, updated_by = excluded.updated_by",
        [flow_id, node_key, float(x), float(y), utc_now_ms(), by],
    )


def save_position_if_absent(flow_id: str, node_key: str, x: float, y: float) -> None:
    """Q-W3b — supersede handoff keeps a position the target already has."""
    get_store()._execute(
        "INSERT INTO graph_node_positions (group_id, node_key, x, y, updated_at, updated_by) "
        "VALUES (?, ?, ?, ?, ?, NULL) ON CONFLICT (group_id, node_key) DO NOTHING",
        [flow_id, node_key, float(x), float(y), utc_now_ms()],
    )


def viewport(flow_id: str, user_id: str) -> Optional[dict]:
    """Q-R2."""
    row = get_store()._fetch_one(
        "SELECT x, y, zoom FROM graph_viewports WHERE group_id = ? AND user_id = ?", [flow_id, user_id]
    )
    if not row:
        return None
    return {"x": float(row["x"]), "y": float(row["y"]), "zoom": float(row["zoom"])}


def save_viewport(flow_id: str, user_id: str, x: float, y: float, zoom: float) -> None:
    """Q-W6."""
    get_store()._execute(
        "INSERT INTO graph_viewports (group_id, user_id, x, y, zoom, updated_at) VALUES (?, ?, ?, ?, ?, ?) "
        "ON CONFLICT (group_id, user_id) DO UPDATE SET x = excluded.x, y = excluded.y, "
        "zoom = excluded.zoom, updated_at = excluded.updated_at",
        [flow_id, user_id, float(x), float(y), float(zoom), utc_now_ms()],
    )


# ── Identity mapping (T04 / T05) ─────────────────────────────────────────────

def identity_record(flow_id: str) -> Optional[dict]:
    """Q-R3. ``None`` when the Flow was never recorded (L0009 E-08)."""
    store = get_store()
    head = store._fetch_one(
        "SELECT sequence_tag FROM graph_identity_records WHERE group_id = ?", [flow_id]
    )
    if head is None:
        return None
    rows = store._fetch_all(
        "SELECT node_key, identity_source, item_id, item_seq, item_type, label, sort_order, "
        "source_wp_card_id, result_doc_id, is_protected FROM graph_identity_rows "
        "WHERE group_id = ? ORDER BY sort_order, item_seq",
        [flow_id],
    )
    return {"sequence_tag": head.get("sequence_tag"), "rows": [dict(r) for r in rows or []]}


def replace_identity(flow_id: str, sequence_tag: Optional[str], rows: Iterable[dict], by: Optional[str]) -> None:
    """Q-W4 — header upsert, then the Flow's mapping rows replaced as a whole."""
    store = get_store()
    store._execute(
        "INSERT INTO graph_identity_records (group_id, sequence_tag, recorded_at, recorded_by) "
        "VALUES (?, ?, ?, ?) ON CONFLICT (group_id) DO UPDATE SET sequence_tag = excluded.sequence_tag, "
        "recorded_at = excluded.recorded_at, recorded_by = excluded.recorded_by",
        [flow_id, sequence_tag, utc_now_ms(), by],
    )
    store._execute("DELETE FROM graph_identity_rows WHERE group_id = ?", [flow_id])
    for r in rows:
        store._execute(
            "INSERT INTO graph_identity_rows (group_id, node_key, identity_source, item_id, item_seq, "
            "item_type, label, sort_order, source_wp_card_id, result_doc_id, is_protected) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                flow_id, r["node_key"], r["identity_source"], int(r["item_id"]), int(r["item_seq"]),
                str(r["item_type"] or "").upper(), str(r["label"] or ""), int(r["sort_order"] or 0),
                r.get("source_wp_card_id"), r.get("result_doc_id"), 1 if r.get("is_protected") else 0,
            ],
        )


# ── Retired keys (T06) ───────────────────────────────────────────────────────

def retired_keys(flow_id: str) -> set[str]:
    """Q-R4."""
    rows = get_store()._fetch_all(
        "SELECT node_key FROM graph_retired_node_keys WHERE group_id = ?", [flow_id]
    )
    return {r["node_key"] for r in rows or []}


def retire(flow_id: str, node_key: str, reason: str) -> None:
    """Q-W5 — idempotent; the first retirement row is kept."""
    if reason not in RETIRE_REASONS:
        raise ValueError(f"unknown retire reason: {reason}")
    get_store()._execute(
        "INSERT INTO graph_retired_node_keys (group_id, node_key, reason, retired_at) VALUES (?, ?, ?, ?) "
        "ON CONFLICT (group_id, node_key) DO NOTHING",
        [flow_id, node_key, reason, utc_now_ms()],
    )
