"""Card line store: which document fills each line of a card (flowgate.v02.0053 T#1).

Migration 145_graph_node_documents.sql. One row per (Flow, node, line, document type). ``owned`` = 1
when the card created the document (it may delete it again while it is a draft); 0 when the line
shows a document the card did not create (the Flow's root, a run result, a line sharing another
line's document). Rows are keyed by node key, so a draft node and the same node of a revision share
them; the document type keeps the lines of each card kind apart, so a node retyped in the draft
(D -> P, TS -> T) adds its new lines without replacing those a confirmed revision of it shows.
The R↔B root conversion rewrites the line holding the root (``retarget``): new doc_id and kind.
Reads ``graph_revision_nodes`` (the Graph's confirmed revisions) and never writes it.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable, Optional

from modules.flow_gate.db.connection import get_store

NODE_DOCUMENT_TABLE = "graph_node_documents"
_COLUMNS = "node_key, slot, doc_type, doc_id, owned, ordinal, created_at, created_by"


def _now() -> str:
    """ISO 8601 UTC with milliseconds, like the other Graph rows (P0008 §1.1)."""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def available() -> bool:
    """False until migration 145 is applied: every card then has no document lines."""
    return get_store().table_exists(NODE_DOCUMENT_TABLE)


def rows(flow_id: str, node_keys: Optional[Iterable[str]] = None) -> list[dict]:
    """The Flow's lines (optionally of some nodes only), node then line order."""
    if not available():
        return []
    found = get_store()._fetch_all(
        f"SELECT {_COLUMNS} FROM graph_node_documents WHERE group_id = ? ORDER BY node_key, ordinal",
        [flow_id],
    )
    out = [dict(r) for r in found or []]
    if node_keys is not None:
        keys = set(node_keys)
        out = [r for r in out if r["node_key"] in keys]
    return out


def insert(flow_id: str, node_key: str, slot: str, doc_type: str, doc_id: str, owned: bool, ordinal: int,
           by: Optional[str]) -> None:
    get_store()._execute(
        f"INSERT INTO graph_node_documents (group_id, {_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [flow_id, node_key, slot, doc_type, doc_id, 1 if owned else 0, ordinal, _now(), by],
    )


def rebind(flow_id: str, node_key: str, slot: str, doc_type: str, doc_id: str) -> int:
    """A line now shows another document (a run result): never owned by the card."""
    return get_store()._execute_affected(
        "UPDATE graph_node_documents SET doc_id = ?, owned = 0 "
        "WHERE group_id = ? AND node_key = ? AND slot = ? AND doc_type = ?",
        [doc_id, flow_id, node_key, slot, doc_type],
    )


def retarget(flow_id: str, node_key: str, slot: str, doc_type: str, new_doc_type: str, doc_id: str) -> int:
    """A line's document took a new identity (R↔B root conversion): the line follows, ownership stays."""
    return get_store()._execute_affected(
        "UPDATE graph_node_documents SET doc_type = ?, doc_id = ? "
        "WHERE group_id = ? AND node_key = ? AND slot = ? AND doc_type = ?",
        [new_doc_type, doc_id, flow_id, node_key, slot, doc_type],
    )


def delete(flow_id: str, node_key: str, slot: str, doc_type: str) -> None:
    get_store()._execute(
        "DELETE FROM graph_node_documents WHERE group_id = ? AND node_key = ? AND slot = ? AND doc_type = ?",
        [flow_id, node_key, slot, doc_type],
    )


def revision_kinds(flow_id: str, node_key: str) -> set[tuple]:
    """``(node_type, doc_type)`` of the node in every confirmed revision: the lines of those kinds
    stay — the revision views and their runs show them."""
    found = get_store()._fetch_all(
        "SELECT DISTINCT node_type, doc_type FROM graph_revision_nodes WHERE group_id = ? AND node_key = ?",
        [flow_id, node_key],
    )
    return {(r["node_type"], r["doc_type"]) for r in found or []}


def doc_ids(flow_id: str, *, owned_only: bool = False) -> set[str]:
    return {r["doc_id"] for r in rows(flow_id) if r["owned"] or not owned_only}


def project_branch(project_id: str) -> str:
    """The project's document branch (same fallback as the v0.1 document routes)."""
    try:
        row = get_store()._fetch_one("SELECT branch FROM project_settings WHERE project_id = ?", [project_id])
    except Exception:  # noqa: BLE001
        return "main"
    return (((row or {}).get("branch")) or "main").strip() or "main"
