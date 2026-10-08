"""``graph_canvas_changed`` publication (P0008 §8). Queued after commit; best-effort delivery."""
from __future__ import annotations

from typing import Optional, Sequence

from modules.flow_gate.api.v1.events.event_types import EventType
from modules.flow_gate.api.v1.events.publisher import FlowEvent, broadcast_event_threadsafe
from modules.flow_gate.db.connection import after_commit


def publish_canvas_changed(
    *,
    project_id: Optional[str],
    flow_id: str,
    reason: str,
    actor_user_id: Optional[str],
    origin_client_id: Optional[str],
    node_keys: Sequence[str] = (),
) -> None:
    event = FlowEvent(
        event_type=EventType.GRAPH_CANVAS_CHANGED.value,
        payload={
            "flow_id": flow_id,
            "reason": reason,
            "actor_user_id": actor_user_id,
            "origin_client_id": origin_client_id,
            "draft_version": None,
            "revision_no": None,
            "node_keys": list(node_keys) if reason == "layout" else [],
        },
        audience="*",
        project=project_id,
        group_id=flow_id,
        doc_id=None,
    )

    def _send() -> None:
        broadcast_event_threadsafe(event)

    if not after_commit(_send):
        _send()
