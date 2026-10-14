"""Graph SSE publication (P0008 §8): ``graph_canvas_changed`` and ``flow_ownership_changed``.

Queued after commit; best-effort delivery (an event is a refresh signal, never the source of
state). Sent through the existing publisher and stream — no new stream (P0008 §8.1).
"""
from __future__ import annotations

from typing import Optional, Sequence

from modules.flow_gate.api.v1.events.event_types import EventType
from modules.flow_gate.api.v1.events.publisher import FlowEvent, broadcast_event_threadsafe
from modules.flow_gate.db.connection import after_commit


def _queue(event: FlowEvent) -> None:
    def _send() -> None:
        broadcast_event_threadsafe(event)

    if not after_commit(_send):
        _send()


def publish_canvas_changed(
    *,
    project_id: Optional[str],
    flow_id: str,
    reason: str,
    actor_user_id: Optional[str],
    origin_client_id: Optional[str],
    node_keys: Sequence[str] = (),
    draft_version: Optional[int] = None,
    revision_no: Optional[int] = None,
) -> None:
    """``reason``: "layout" | "draft" | "revision" | "identity_rematch" (P0008 §8.3) | "hold" (0061 T#11: a card's
    Hold was set / released, with or without a run; ``node_keys`` names the card)."""
    _queue(FlowEvent(
        event_type=EventType.GRAPH_CANVAS_CHANGED.value,
        payload={
            "flow_id": flow_id,
            "reason": reason,
            "actor_user_id": actor_user_id,
            "origin_client_id": origin_client_id,
            "draft_version": draft_version if reason == "draft" else None,
            "revision_no": revision_no if reason == "revision" else None,
            "node_keys": list(node_keys) if reason in ("layout", "hold") else [],
        },
        audience="*",
        project=project_id,
        group_id=flow_id,
        doc_id=None,
    ))


def publish_ownership_changed(
    *,
    project_id: Optional[str],
    flow_id: str,
    to: str,
    actor_user_id: Optional[str],
    origin_client_id: Optional[str] = None,
    revision_no: Optional[int] = None,
    sequence_root_doc_id: Optional[str] = None,
    root_binding: Optional[dict] = None,
) -> None:
    """unassigned -> ``to`` ("graph" on confirmation, "v01" on a guarded v0.1 decide)."""
    binding = {"node_key": root_binding["node_key"], "doc_id": root_binding["doc_id"]} if root_binding else None
    _queue(FlowEvent(
        event_type=EventType.FLOW_OWNERSHIP_CHANGED.value,
        payload={
            "flow_id": flow_id,
            "from": "unassigned",
            "to": to,
            "revision_no": revision_no if to == "graph" else None,
            "sequence_root_doc_id": sequence_root_doc_id if to == "v01" else None,
            "root_binding": binding,
            "actor_user_id": actor_user_id,
            "origin_client_id": origin_client_id if to == "graph" else None,
        },
        audience="*",
        project=project_id,
        group_id=flow_id,
        doc_id=sequence_root_doc_id if to == "v01" else (binding or {}).get("doc_id"),
    ))
