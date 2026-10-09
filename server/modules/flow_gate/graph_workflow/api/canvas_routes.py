"""Canvas read + layout save routes (P0008 §3 S1, §4 S2). Thin: no judgement lives here."""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Body, Request

from modules.flow_gate.graph_workflow.domain.layout_request import parse_layout
from modules.flow_gate.graph_workflow.services import canvas_service, layout_service

from . import envelope

router = APIRouter(prefix="/api/v1", tags=["GraphCanvas"])


@router.get("/flows/{flow_id}/canvas")
def get_flow_canvas(flow_id: str, request: Request, view: Optional[str] = None, collapse: Optional[str] = None):
    def handle() -> dict:
        user_id = envelope.authenticate(request)
        client_id = envelope.client_id_of(request)
        if view is not None and view not in canvas_service.VIEW_MODES:
            raise envelope.invalid(request, [{"field": "view", "reason": "not_one_of:revision,draft"}])
        group = canvas_service.load_flow(flow_id)
        envelope.authorize(user_id, group, "read")
        return canvas_service.read_canvas(group, user_id, view=view, collapse=collapse, client_id=client_id)

    return envelope.run(request, handle)


@router.put("/flows/{flow_id}/canvas/layout")
def put_flow_canvas_layout(flow_id: str, request: Request, body: Any = Body(default=None)):
    def handle() -> dict:
        user_id = envelope.authenticate(request)
        client_id = envelope.client_id_of(request)
        req = parse_layout(body)
        if req.errors:
            raise envelope.invalid(request, req.errors, "empty_layout" if req.only_empty else "invalid_request")
        group = canvas_service.load_flow(flow_id)
        envelope.authorize(user_id, group, "write")
        return layout_service.save_layout(group, user_id, req, client_id)

    return envelope.run(request, handle)
