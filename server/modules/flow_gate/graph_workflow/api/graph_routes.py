"""Graph definition routes (P0008 §5 S3, §6 S4) and the card title save (0064 P0006 §4.2.2).
Thin: no judgement lives here."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Request

from modules.flow_gate.graph_workflow.domain.draft_request import parse_draft, parse_version, sort_errors
from modules.flow_gate.graph_workflow.services import canvas_service, confirm_service, graph_service, node_title

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


def _title_of(body: Any) -> tuple[Any, list[dict]]:
    """The ``title`` of a title save body; anything else in it (a state version too) is ignored."""
    if not isinstance(body, dict):
        return None, [{"field": "body", "reason": "not_an_object"}]
    if "title" not in body:
        return None, [{"field": "title", "reason": "required"}]
    if not isinstance(body["title"], str):
        return None, [{"field": "title", "reason": "not_a_string"}]
    return body["title"], []


@router.put("/flows/{flow_id}/graph/nodes/{node_key}/title")
def put_flow_graph_node_title(flow_id: str, node_key: str, request: Request, body: Any = Body(default=None)):
    def handle() -> dict:
        user_id = envelope.authenticate(request)
        client_id = envelope.client_id_of(request)
        group = canvas_service.load_flow(flow_id)
        envelope.authorize(user_id, group, "write")
        title, errors = _title_of(body)
        if errors:
            raise envelope.invalid(request, errors)
        return node_title.save_human_title(group, node_key, title, user_id, client_id)

    return envelope.run(request, handle)
