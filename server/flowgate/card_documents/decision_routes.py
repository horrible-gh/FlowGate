"""Graph-only user decision API. Mounted by the existing card document router."""
from typing import Any
from fastapi import APIRouter, Body, Request
from modules.flow_gate.graph_workflow.api import envelope
from modules.flow_gate.graph_workflow.services import canvas_service
from . import decisions

router = APIRouter()


@router.get("/flows/{flow_id}/nodes/{node_key}/documents/decisions")
def get_decisions(flow_id: str, node_key: str, request: Request):
    def handle():
        user_id = envelope.authenticate(request)
        group = canvas_service.load_flow(flow_id)
        envelope.authorize(user_id, group, "read")
        return decisions.read(group, node_key, user_id)
    return envelope.run(request, handle)


@router.post("/flows/{flow_id}/nodes/{node_key}/documents/{slot}/decision")
def decide(flow_id: str, node_key: str, slot: str, request: Request, body: Any = Body(default=None)):
    def handle():
        user_id = envelope.authenticate(request)
        errors = decisions.parse_body(body)
        if errors:
            raise envelope.invalid(request, errors)
        group = canvas_service.load_flow(flow_id)
        return decisions.write(group, node_key, slot, body, user_id)
    return envelope.run(request, handle)
