"""Graph definition store: one draft per Flow and immutable confirmed revisions.

DB0010 T07~T12 (migration 142_graph_definitions.sql), queries §4.3 Q-R5 / Q-R6 / Q-R6b / Q-W7 /
Q-W8 ①~③⑦. A draft is replaced as a whole on every save; a revision row and its nodes / edges are
inserted once and never updated or deleted here (DB0010 I-05). Coordinates are not stored here.
"""
from __future__ import annotations

import json
from typing import Any, Optional

from modules.flow_gate.db.connection import get_store

from .layout_store import utc_now_ms

DEFINITION_TABLE = "graph_drafts"

_NODE_COLUMNS = (
    "node_key, ordinal, node_type, doc_type, palette_item_id, label, "
    "assignee_kind, assignee_user_id, assignee_provider_id"
)
_EDGE_COLUMNS = "edge_id, ordinal, source_node_key, target_node_key, condition_json"


def available() -> bool:
    """False until migration 142 is applied (callers then see "no draft, no revision")."""
    return get_store().table_exists(DEFINITION_TABLE)


def _node(row: dict) -> dict:
    return {k: row.get(k) for k in (
        "node_key", "node_type", "doc_type", "palette_item_id", "label",
        "assignee_kind", "assignee_user_id", "assignee_provider_id",
    )}


def _edge(row: dict) -> dict:
    raw = row.get("condition_json")
    return {
        "edge_id": row["edge_id"],
        "source": row["source_node_key"],
        "target": row["target_node_key"],
        "condition": json.loads(raw) if raw else None,
    }


# ── Drafts (T07~T09) ─────────────────────────────────────────────────────────

def draft_header(flow_id: str) -> Optional[dict]:
    """Q-R5 header."""
    if not available():
        return None
    row = get_store()._fetch_one(
        "SELECT group_id, draft_version, base_revision_no, created_at, created_by, updated_at, updated_by "
        "FROM graph_drafts WHERE group_id = ?",
        [flow_id],
    )
    return dict(row) if row else None


def draft(flow_id: str) -> Optional[dict]:
    """Q-R5: header + nodes + edges in request order, or None."""
    head = draft_header(flow_id)
    if head is None:
        return None
    store = get_store()
    nodes = store._fetch_all(
        f"SELECT {_NODE_COLUMNS} FROM graph_draft_nodes WHERE group_id = ? ORDER BY ordinal", [flow_id]
    )
    edges = store._fetch_all(
        f"SELECT {_EDGE_COLUMNS} FROM graph_draft_edges WHERE group_id = ? ORDER BY ordinal", [flow_id]
    )
    return {**head, "nodes": [_node(dict(r)) for r in nodes or []], "edges": [_edge(dict(r)) for r in edges or []]}


def create_draft(flow_id: str, base_revision_no: Optional[int], by: Optional[str]) -> int:
    """Q-W7 first save (``draft_version = 0`` request). Returns affected rows (1, or 0 if one exists)."""
    now = utc_now_ms()
    return get_store()._execute_affected(
        "INSERT INTO graph_drafts (group_id, draft_version, base_revision_no, created_at, created_by, "
        "updated_at, updated_by) VALUES (?, 1, ?, ?, ?, ?, ?) ON CONFLICT (group_id) DO NOTHING",
        [flow_id, base_revision_no, now, by, now, by],
    )


def bump_draft(flow_id: str, expected_version: int, by: Optional[str]) -> int:
    """Q-W7 later save: conditional version bump. 0 rows = ``draft_version_conflict``."""
    return get_store()._execute_affected(
        "UPDATE graph_drafts SET draft_version = draft_version + 1, updated_at = ?, updated_by = ? "
        "WHERE group_id = ? AND draft_version = ?",
        [utc_now_ms(), by, flow_id, expected_version],
    )


def replace_draft_content(flow_id: str, nodes: list[dict], edges: list[dict]) -> None:
    """Q-W7 tail: the draft's nodes and edges replaced as a whole, ordinal = request order."""
    store = get_store()
    store._execute("DELETE FROM graph_draft_edges WHERE group_id = ?", [flow_id])
    store._execute("DELETE FROM graph_draft_nodes WHERE group_id = ?", [flow_id])
    for ordinal, n in enumerate(nodes):
        store._execute(
            f"INSERT INTO graph_draft_nodes (group_id, {_NODE_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [flow_id, n["node_key"], ordinal, n["node_type"], n.get("doc_type"), n.get("palette_item_id"),
             n["label"], n.get("assignee_kind"), n.get("assignee_user_id"), n.get("assignee_provider_id")],
        )
    for ordinal, e in enumerate(edges):
        condition = e.get("condition")
        store._execute(
            f"INSERT INTO graph_draft_edges (group_id, {_EDGE_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?)",
            [flow_id, e["edge_id"], ordinal, e["source"], e["target"],
             None if condition is None else json.dumps(condition, ensure_ascii=False, sort_keys=True)],
        )


def delete_draft(flow_id: str) -> None:
    """Q-W8 ⑦: nodes and edges go with it (CASCADE, and explicitly for FK-off SQLite connections)."""
    store = get_store()
    store._execute("DELETE FROM graph_draft_edges WHERE group_id = ?", [flow_id])
    store._execute("DELETE FROM graph_draft_nodes WHERE group_id = ?", [flow_id])
    store._execute("DELETE FROM graph_drafts WHERE group_id = ?", [flow_id])


# ── Revisions (T10~T12) ──────────────────────────────────────────────────────

def latest_revision_no(flow_id: str) -> Optional[int]:
    """Q-R6."""
    if not available():
        return None
    row = get_store()._fetch_one(
        "SELECT MAX(revision_no) AS latest FROM graph_revisions WHERE group_id = ?", [flow_id]
    )
    value = (row or {}).get("latest")
    return int(value) if value is not None else None


def revision(flow_id: str, revision_no: int) -> Optional[dict]:
    """Q-R6b: header + nodes + edges, or None."""
    if not available():
        return None
    store = get_store()
    head = store._fetch_one(
        "SELECT group_id, revision_no, source_draft_version, base_revision_no, confirmed_at, confirmed_by "
        "FROM graph_revisions WHERE group_id = ? AND revision_no = ?",
        [flow_id, revision_no],
    )
    if head is None:
        return None
    nodes = store._fetch_all(
        f"SELECT {_NODE_COLUMNS} FROM graph_revision_nodes WHERE group_id = ? AND revision_no = ? ORDER BY ordinal",
        [flow_id, revision_no],
    )
    edges = store._fetch_all(
        f"SELECT {_EDGE_COLUMNS} FROM graph_revision_edges WHERE group_id = ? AND revision_no = ? ORDER BY ordinal",
        [flow_id, revision_no],
    )
    return {**dict(head), "nodes": [_node(dict(r)) for r in nodes or []],
            "edges": [_edge(dict(r)) for r in edges or []]}


def insert_revision_from_draft(flow_id: str, revision_no: int, draft_version: int,
                               base_revision_no: Optional[int], by: Optional[str]) -> str:
    """Q-W8 ①~③: the revision header, then the draft's nodes and edges copied as they are.

    ``INSERT … SELECT`` keeps draft and revision content from diverging. The revision edge FKs
    reject a dangling edge (validation refuses one first). Returns ``confirmed_at``.
    """
    store = get_store()
    now = utc_now_ms()
    store._execute(
        "INSERT INTO graph_revisions (group_id, revision_no, source_draft_version, base_revision_no, "
        "confirmed_at, confirmed_by) VALUES (?, ?, ?, ?, ?, ?)",
        [flow_id, revision_no, draft_version, base_revision_no, now, by],
    )
    store._execute(
        f"INSERT INTO graph_revision_nodes (group_id, revision_no, {_NODE_COLUMNS}) "
        f"SELECT group_id, ?, {_NODE_COLUMNS} FROM graph_draft_nodes WHERE group_id = ?",
        [revision_no, flow_id],
    )
    store._execute(
        f"INSERT INTO graph_revision_edges (group_id, revision_no, {_EDGE_COLUMNS}) "
        f"SELECT group_id, ?, {_EDGE_COLUMNS} FROM graph_draft_edges WHERE group_id = ?",
        [revision_no, flow_id],
    )
    return now


def as_graph_parts(record: Optional[dict[str, Any]]) -> tuple[list[dict], list[dict]]:
    if not record:
        return [], []
    return list(record.get("nodes") or []), list(record.get("edges") or [])
