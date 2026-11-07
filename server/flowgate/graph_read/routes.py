"""0145 AI read transport. Only two GET routes; v1 remains user-only."""
from fastapi import APIRouter, Request
from modules.flow_gate.graph_workflow.api import envelope
from modules.flow_gate.graph_workflow.services.errors import GraphApiError
from . import authz, service

router = APIRouter(prefix="/api/v2/ai", tags=["AiGraphRead"])


def response(request, handler):
    try:
        return envelope.ok(handler())
    except GraphApiError as exc:
        return envelope.fail(request, exc.status, exc.code, exc.params, exc.code)
    except Exception:
        # No exception repr: auth/storage/provider exceptions can contain credentials.
        return envelope.fail(request, 500, "internal_error")


def _get(request, flow_id, resource):
    raw = request.headers.get("Authorization", "")
    token = authz.verify(raw[7:].strip() if raw.startswith("Bearer ") else None)
    allowed = {"view"} if resource == "canvas" else set()
    if set(request.query_params) - allowed or any(len(request.query_params.getlist(k)) > 1 for k in allowed):
        authz.refuse("invalid_request", 422)
    return service.read(token, flow_id, resource, request.query_params.get("view"))


@router.get("/flows/{flow_id}/canvas")
def canvas(flow_id: str, request: Request):
    return response(request, lambda: _get(request, flow_id, "canvas"))


@router.get("/flows/{flow_id}/runs/current")
def current(flow_id: str, request: Request):
    return response(request, lambda: _get(request, flow_id, "current"))
