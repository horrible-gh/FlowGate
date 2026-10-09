"""Layout save (P0008 §4 ``PUT /flows/{flow_id}/canvas/layout``, L0009 §2.3.8).

Positions are Flow-wide and per node identity (last write wins, no version check); the
viewport is per user. Allowed for every ownership state. Key classification and the writes
happen in one Flow guard.
"""
from __future__ import annotations

from typing import Optional

from modules.flow_gate.graph_workflow.domain.layout_request import LayoutRequest
from modules.flow_gate.graph_workflow.store import layout_store

from .canvas_events import publish_canvas_changed
from .canvas_service import current_node_keys
from .flow_guard import with_flow_guard
from .graph_service import require_writable
from .identity_keeper import ensure_identity


def save_layout(group: dict, user_id: str, req: LayoutRequest, client_id: Optional[str]) -> dict:
    flow_id = group["group_id"]
    project_id = group.get("project_id")
    require_writable(flow_id)
    # Bring the node identities up to date first (a v0.1 edit since the last Canvas read).
    ensure_identity(flow_id, project_id, user_id, client_id)

    def body() -> dict:
        keys = current_node_keys(flow_id)
        retired = layout_store.retired_keys(flow_id)
        saved: list[str] = []
        ignored: list[dict] = []
        for pos in req.positions:
            key = pos["node_key"]
            if key in keys:
                layout_store.save_position(flow_id, key, pos["x"], pos["y"], user_id)
                saved.append(key)
            else:
                ignored.append({"node_key": key, "reason": "retired" if key in retired else "unknown_node_key"})
        if req.viewport is not None:
            vp = req.viewport
            layout_store.save_viewport(flow_id, user_id, vp["x"], vp["y"], vp["zoom"])
        if saved:
            publish_canvas_changed(
                project_id=project_id, flow_id=flow_id, reason="layout", actor_user_id=user_id,
                origin_client_id=client_id, node_keys=list(dict.fromkeys(saved)),
            )
        return {
            "flow_id": flow_id,
            "saved_positions": len(saved),
            "viewport_saved": req.viewport is not None,
            "ignored": ignored,
        }

    return with_flow_guard(flow_id, body)
