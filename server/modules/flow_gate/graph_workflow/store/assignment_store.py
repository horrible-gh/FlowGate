"""Engine assignment store: the Graph assignment record and the root binding.

DB0010 T13 / T14 (migration 143_graph_engine_assignment.sql), queries §4.3 Q-R6c and Q-W8 ④⑤.
At most one assignment and one binding per Flow; neither is ever updated or removed here
(D0004 §3.1 "배정은 되돌리지 않는다"). v0.1 assignment is never recorded — the v0.1 sequence
itself is its evidence.
"""
from __future__ import annotations

from typing import Optional

from modules.flow_gate.db.connection import get_store

from .layout_store import utc_now_ms

ASSIGNMENT_TABLE = "graph_assignments"


def available() -> bool:
    """False until migration 143 is applied: every Flow then has "no assignment record"."""
    return get_store().table_exists(ASSIGNMENT_TABLE)


def assignment(flow_id: str) -> Optional[dict]:
    """Q-R6c."""
    if not available():
        return None
    row = get_store()._fetch_one(
        "SELECT group_id, assigned_revision_no, assigned_at, assigned_by FROM graph_assignments WHERE group_id = ?",
        [flow_id],
    )
    return dict(row) if row else None


def root_binding(flow_id: str) -> Optional[dict]:
    """Q-R6c."""
    if not available():
        return None
    row = get_store()._fetch_one(
        "SELECT group_id, node_key, doc_id, doc_type, bound_at_revision_no, bound_at, bound_by "
        "FROM graph_root_bindings WHERE group_id = ?",
        [flow_id],
    )
    return dict(row) if row else None


def insert_assignment(flow_id: str, revision_no: int, assigned_at: str, by: Optional[str]) -> None:
    """Q-W8 ④ — after the revision it points at (FK)."""
    get_store()._execute(
        "INSERT INTO graph_assignments (group_id, assigned_revision_no, assigned_at, assigned_by) VALUES (?, ?, ?, ?)",
        [flow_id, revision_no, assigned_at, by],
    )


def insert_root_binding(flow_id: str, node_key: str, doc_id: str, doc_type: str, revision_no: int,
                        by: Optional[str]) -> None:
    """Q-W8 ⑤ — after the assignment and the revision node it points at (FK)."""
    get_store()._execute(
        "INSERT INTO graph_root_bindings (group_id, node_key, doc_id, doc_type, bound_at_revision_no, bound_at, "
        "bound_by) VALUES (?, ?, ?, ?, ?, ?, ?)",
        [flow_id, node_key, doc_id, doc_type, revision_no, utc_now_ms(), by],
    )
