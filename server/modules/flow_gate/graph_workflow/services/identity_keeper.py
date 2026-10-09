"""Node Identity Keeper (D0004 §3.6, L0009 §2.3.3 / §2.3.7, P0008 S2-4).

Called on every Canvas read. When the recorded ``sequence_tag`` still equals the v0.1
``build_workflow_tag`` and no supersede handoff is pending, the recorded mapping is used and
nothing is written. Otherwise the Flow guard is taken, the state re-read, and the rematch /
handoff result recorded in the layout store only — v0.1 data is never written.
"""
from __future__ import annotations

import secrets
import string
from dataclasses import dataclass, field
from typing import Optional

from modules.flow_gate.graph_workflow.domain.rematch import doc_key, rematch, row_key
from modules.flow_gate.graph_workflow.domain.supersede import chain_reaches, pending_handoffs
from modules.flow_gate.graph_workflow.projection.flow_snapshot import (
    FlowSnapshot,
    protected_ids,
    read_snapshot,
)
from modules.flow_gate.graph_workflow.store import layout_store

from .canvas_events import publish_canvas_changed
from .flow_guard import with_flow_guard

SLOT_KEY_PREFIX = "slot_"
_SLOT_ALPHABET = string.digits + string.ascii_lowercase


@dataclass
class IdentityResult:
    snapshot: FlowSnapshot
    row_keys: dict[int, tuple[str, str]]          # item_id -> (node_key, identity_source)
    protected: set[int]
    sequence_tag: Optional[str] = None
    rematched: bool = False
    retired: list[str] = field(default_factory=list)


def sequence_tag_of(snapshot: FlowSnapshot) -> Optional[str]:
    if snapshot.sequence is None:
        return None
    from modules.flow_gate.services.work_plan_apply_service import build_workflow_tag
    return build_workflow_tag(snapshot.sequence, snapshot.rows)


def keys_from_record(rows: list[dict], record: Optional[dict], protected: set[int]) -> dict[int, tuple[str, str]]:
    """Keys of the current rows from a record that still matches them (no write)."""
    slots = {
        int(r["item_id"]): r["node_key"]
        for r in (record or {}).get("rows", []) if r.get("identity_source") == "sequence_slot"
    }
    keys: dict[int, tuple[str, str]] = {}
    for row in rows:
        item_id = int(row["id"])
        if row.get("result_doc_id") is not None:
            keys[item_id] = (doc_key(row["result_doc_id"]), "document")
        elif item_id in protected:
            keys[item_id] = (row_key(item_id), "sequence_row")
        elif item_id in slots:
            keys[item_id] = (slots[item_id], "sequence_slot")
        else:
            # Not reachable while the tags match; the S1 row key keeps the Canvas readable.
            keys[item_id] = (row_key(item_id), "sequence_row")
    return _dedupe(keys)


def _dedupe(keys: dict[int, tuple[str, str]]) -> dict[int, tuple[str, str]]:
    """Two rows can never share a node identity (DB0010 I-16), even on damaged v0.1 data
    where two slots hold the same result document: the later row falls back to its row key."""
    seen: set[str] = set()
    for item_id in sorted(keys):
        key, source = keys[item_id]
        if key in seen:
            keys[item_id] = (row_key(item_id), "sequence_row")
        seen.add(keys[item_id][0])
    return keys


def ensure_identity(flow_id: str, project_id: Optional[str], actor: Optional[str], client_id: Optional[str],
                    snapshot: Optional[FlowSnapshot] = None) -> IdentityResult:
    snap = snapshot or read_snapshot(flow_id)
    record = layout_store.identity_record(flow_id)
    recorded_tag = record["sequence_tag"] if record else None
    handoffs = pending_handoffs(
        flow_id, snap.docs_by_id, layout_store.positions(flow_id).keys(), layout_store.retired_keys(flow_id)
    )
    if recorded_tag == sequence_tag_of(snap) and not handoffs:
        protected = protected_ids(snap.rows)
        return IdentityResult(snap, keys_from_record(snap.rows, record, protected), protected, recorded_tag)
    return with_flow_guard(flow_id, lambda: _record(flow_id, project_id, actor, client_id))


def _record(flow_id: str, project_id: Optional[str], actor: Optional[str], client_id: Optional[str]) -> IdentityResult:
    snap = read_snapshot(flow_id)                      # re-read inside the guard (L0009 §2.1.1)
    tag = sequence_tag_of(snap)
    record = layout_store.identity_record(flow_id)
    recorded_tag = record["sequence_tag"] if record else None
    saved = layout_store.positions(flow_id)
    retired = layout_store.retired_keys(flow_id)
    docs = snap.docs_by_id
    protected = protected_ids(snap.rows)
    if recorded_tag == tag and not pending_handoffs(flow_id, docs, saved.keys(), retired):
        return IdentityResult(snap, keys_from_record(snap.rows, record, protected), protected, recorded_tag)

    retired_now: list[str] = []

    def retire(key: str, reason: str) -> None:
        if key not in retired:
            layout_store.retire(flow_id, key, reason)
            retired.add(key)
            retired_now.append(key)

    if recorded_tag != tag:
        taken = set(saved) | retired | {r["node_key"] for r in (record or {}).get("rows", [])}
        result = rematch(
            (record or {}).get("rows", []), snap.rows, protected,
            new_slot_key=lambda: _new_slot_key(taken),
            supersede_reaches=lambda old, new: chain_reaches(old, new, docs),
        )
        row_keys = _dedupe({int(a.row["id"]): (a.node_key, a.identity_source) for a in result.assignments})
        in_use = {key for key, _ in row_keys.values()}
        for from_key, to_key in result.handoffs:
            if from_key in saved:
                layout_store.save_position(flow_id, to_key, *saved[from_key], None)
                saved[to_key] = saved[from_key]
            if from_key not in in_use:
                retire(from_key, "rematch")
        for key in result.retired_keys:
            retire(key, "rematch")
        layout_store.replace_identity(flow_id, tag, _record_rows(snap.rows, row_keys, protected), actor)
    else:
        row_keys = keys_from_record(snap.rows, record, protected)

    for h in pending_handoffs(flow_id, docs, saved.keys(), retired):
        if h["to_key"] not in saved:
            layout_store.save_position_if_absent(flow_id, h["to_key"], *saved[h["from_key"]])
            saved[h["to_key"]] = saved[h["from_key"]]
        retire(h["from_key"], "supersede")

    publish_canvas_changed(
        project_id=project_id, flow_id=flow_id, reason="identity_rematch",
        actor_user_id=actor, origin_client_id=client_id,
    )
    return IdentityResult(snap, row_keys, protected, tag, True, retired_now)


def _record_rows(rows: list[dict], row_keys: dict[int, tuple[str, str]], protected: set[int]) -> list[dict]:
    out = []
    for row in rows:
        key, source = row_keys[int(row["id"])]
        out.append({
            "node_key": key,
            "identity_source": source,
            "item_id": int(row["id"]),
            "item_seq": int(row.get("item_seq") or 0),
            "item_type": row.get("type"),
            "label": row.get("label"),
            "sort_order": int(row.get("sort_order") or 0),
            "source_wp_card_id": row.get("source_wp_card_id"),
            "result_doc_id": row.get("result_doc_id"),
            "is_protected": int(row["id"]) in protected,
        })
    return out


def _new_slot_key(taken: set[str]) -> str:
    while True:
        key = SLOT_KEY_PREFIX + "".join(secrets.choice(_SLOT_ALPHABET) for _ in range(12))
        if key not in taken:
            taken.add(key)
            return key
