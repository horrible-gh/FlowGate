"""Graph definition routes (P0008 §5 S3, §6 S4). Thin: no judgement lives here."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Request

from modules.flow_gate.graph_workflow.domain.draft_request import parse_draft, parse_version, sort_errors
from modules.flow_gate.graph_workflow.services import canvas_service, confirm_service, graph_service

from . import envelope
from .graph_messages import fill_issue_messages

router = APIRouter(prefix="/api/v1", tags=["GraphDefinition"])


@router.get("/flows/{flow_id}/graph")
def get_flow_graph(flow_id: str, request: Request):
    def handle() -> dict:
        user_id = envelope.authenticate(request)
        envelope.client_id_of(request)
        envelope.authorize(user_id, canvas_service.load_flow(flow_id), "read")
        return graph_service.graph_payload(flow_id)

    return envelope.run(request, handle)


@router.get("/flows/{flow_id}/graph/revisions/{revision_no}")
def get_flow_graph_revision(flow_id: str, revision_no: str, request: Request):
    def handle() -> dict:
        user_id = envelope.authenticate(request)
        envelope.client_id_of(request)
        if not (revision_no.isascii() and revision_no.isdigit()) or int(revision_no) < 1:
            raise envelope.invalid(request, [{"field": "revision_no", "reason": "not_positive_integer"}])
        group = canvas_service.load_flow(flow_id)
        envelope.authorize(user_id, group, "read")
        return graph_service.revision_payload(flow_id, int(revision_no))

    return envelope.run(request, handle)


@router.put("/flows/{flow_id}/graph/draft")
def put_flow_graph_draft(flow_id: str, request: Request, body: Any = Body(default=None)):
    def handle() -> dict:
        user_id = envelope.authenticate(request)
        client_id = envelope.client_id_of(request)
        req = parse_draft(body)
        if req.draft_version is None:
            raise envelope.invalid(request, sort_errors(req.errors))
        group = canvas_service.load_flow(flow_id)
        envelope.authorize(user_id, group, "write")
        return graph_service.save_draft(group, user_id, req, client_id)

    return envelope.run(request, handle)


@router.post("/flows/{flow_id}/graph/draft/validate")
def post_flow_graph_validate(flow_id: str, request: Request, body: Any = Body(default=None)):
    def handle() -> dict:
        user_id = envelope.authenticate(request)
        envelope.client_id_of(request)
        version, errors = parse_version(body, minimum=1)
        if errors:
            raise envelope.invalid(request, errors)
        group = canvas_service.load_flow(flow_id)
        envelope.authorize(user_id, group, "write")
        result = graph_service.validate_draft(group, user_id, version)
        return {**result, "issues": fill_issue_messages(result["issues"], envelope.locale_of(request))}

    return envelope.run(request, handle)


@router.post("/flows/{flow_id}/graph/revisions")
def post_flow_graph_revision(flow_id: str, request: Request, body: Any = Body(default=None)):
    def handle() -> dict:
        user_id, client_id = envelope.authenticate(request), envelope.client_id_of(request)
        version, errors = parse_version(body, minimum=1)
        base = body.get("base_revision_no") if isinstance(body, dict) else None
        if base is not None and (not isinstance(base, int) or isinstance(base, bool) or base < 1):
            errors.append({"field": "base_revision_no", "reason": "not_positive_integer_or_null"})
        if errors:
            raise envelope.invalid(request, errors)
        group = canvas_service.load_flow(flow_id)
        envelope.authorize(user_id, group, "write")
        return confirm_service.confirm_revision(group, user_id, version, base, client_id)

    return envelope.run(request, handle, status=201)
