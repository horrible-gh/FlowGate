"""Card settings store (flowgate.v02.0053 T#2).

Migration 146_graph_node_settings.sql. One row per (Flow, node, field) holding the JSON value; no row
= the field's default. Rows are keyed by node key, so a card keeps its settings whether it is only in
the draft or in confirmed revisions (a removed node's key is retired and never reused, and the Flow's
rows go with the Flow). A value is replaced on its own, inside the caller's Flow guard.
0061 T#11: Hold is the field ``hold`` (true) of the same table, kept by node key across revisions like any setting.

Reads ``graph_draft_nodes`` / ``graph_revision_nodes`` (which card a node key is) and never writes them.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional

from modules.flow_gate.db.connection import get_store

SETTINGS_TABLE = "graph_node_settings"
_NODE_COLUMNS = "node_key, node_type, doc_type, label"


def _now() -> str:
    """ISO 8601 UTC with milliseconds, like the other Graph rows (P0008 §1.1)."""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def available() -> bool:
    """False until migration 146 is applied: every card then shows its defaults and runs as before."""
    return get_store().table_exists(SETTINGS_TABLE)


def values(flow_id: str, node_key: str) -> dict[str, Any]:
    """``field -> value`` stored for one card (a row that does not parse is left out)."""
    if not available():
        return {}
    found = get_store()._fetch_all(
        "SELECT field, value_json FROM graph_node_settings WHERE group_id = ? AND node_key = ?",
        [flow_id, node_key],
    )
    out: dict[str, Any] = {}
    for row in found or []:
        try:
            out[row["field"]] = json.loads(row["value_json"])
        except (TypeError, ValueError):
            continue
    return out


def keys_with(flow_id: str, field: str, value: Any) -> frozenset:
    """0061 T#11: the node keys of the Flow whose ``field`` is stored as ``value`` (Hold: ``hold`` = true)."""
    if not available():
        return frozenset()
    found = get_store()._fetch_all(
        "SELECT node_key, value_json FROM graph_node_settings WHERE group_id = ? AND field = ?", [flow_id, field])
    out = set()
    for row in found or []:
        try:
            stored = json.loads(row["value_json"])
        except (TypeError, ValueError):
            continue
        if type(stored) is type(value) and stored == value:          # true, never 1
            out.add(row["node_key"])
    return frozenset(out)


def put(flow_id: str, node_key: str, field: str, value: Any, by: Optional[str]) -> None:
    """Replace one value (delete + insert: the same statements on every dialect)."""
    store = get_store()
    store._execute("DELETE FROM graph_node_settings WHERE group_id = ? AND node_key = ? AND field = ?",
                   [flow_id, node_key, field])
    store._execute(
        "INSERT INTO graph_node_settings (group_id, node_key, field, value_json, updated_at, updated_by) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [flow_id, node_key, field, json.dumps(value, ensure_ascii=False), _now(), by],
    )


def remove(flow_id: str, node_key: str, field: str) -> None:
    """Back to the default."""
    get_store()._execute("DELETE FROM graph_node_settings WHERE group_id = ? AND node_key = ? AND field = ?",
                         [flow_id, node_key, field])


def card_node(flow_id: str, node_key: str) -> Optional[dict]:
    """The card a node key is: its draft node, else its node in the latest revision that has it.

    ``in_draft`` tells which; None when the Flow's Graph definition has no such node.
    """
    store = get_store()
    if not store.table_exists("graph_draft_nodes"):
        return None
    row = store._fetch_one(
        f"SELECT {_NODE_COLUMNS} FROM graph_draft_nodes WHERE group_id = ? AND node_key = ?", [flow_id, node_key])
    if row:
        return {**dict(row), "in_draft": True}
    row = store._fetch_one(
        f"SELECT {_NODE_COLUMNS} FROM graph_revision_nodes WHERE group_id = ? AND node_key = ? "
        "ORDER BY revision_no DESC LIMIT 1",
        [flow_id, node_key],
    )
    return {**dict(row), "in_draft": False} if row else None
