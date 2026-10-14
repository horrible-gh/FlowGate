"""HTTP adapter of the Graph Time Machine (0061 T#13). Mounted under the ``/flowgate`` context next to the Graph run
routes, whose path rule it follows (``/runs/{run_id}/nodes/{node_key}/gate-decision``, ``.../approval``); same
envelope (P0008 §1.2 / §1.3), user JWT only, RBAC write.

    POST /api/v1/runs/{run_id}/nodes/{node_key}/time-machine        (no body, or {})

200 ``{"time_machine": {...}, "run": {...}}``; 409 ``graph_time_machine_blocked`` (the T#12 assessment with its
reasons, nothing changed), ``graph_time_machine_in_progress`` (another operation unfinished, via the blocked reason
``previous_operation_incomplete``) or ``graph_time_machine_partial_failed`` (Git applied, the DB step failed: the Flow
stays refused until the server start finishes it). The entry point on the screen is a done card's context menu (T#15).
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Request

from modules.flow_gate.graph_workflow.api import envelope
from modules.flow_gate.graph_workflow.services import canvas_service, run_service

from . import operation

router = APIRouter(prefix="/api/v1", tags=["GraphTimeMachine"])


def parse_body(body: Any) -> list[dict]:
    if body is None:
        return []
    if not isinstance(body, dict):
        return [{"field": "body", "reason": "not_an_object"}]
    return [{"field": key, "reason": "unknown_field"} for key in sorted(body)]


@router.post("/runs/{run_id}/nodes/{node_key}/time-machine")
def post_time_machine(run_id: str, node_key: str, request: Request, body: Any = Body(default=None)):
    def handle() -> dict:
        user_id = envelope.authenticate(request)
        envelope.client_id_of(request)
        errors = parse_body(body)
        if errors:
            raise envelope.invalid(request, errors)
        group = canvas_service.load_flow(run_service.run_flow_id(run_id))
        envelope.authorize(user_id, group, "write")
        return operation.execute(group, run_id, node_key, user_id, envelope.locale_of(request))

    return envelope.run(request, handle)
