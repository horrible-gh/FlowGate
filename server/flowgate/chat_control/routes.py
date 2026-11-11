"""CLI chat transport of the control tower (stage 1). Authentication runs before the body is parsed."""
from __future__ import annotations

import json

from fastapi import APIRouter, Request
from starlette.concurrency import run_in_threadpool

from modules.flow_gate.graph_workflow.api import envelope
from modules.flow_gate.graph_workflow.services.errors import GraphApiError

from . import authz, service

router = APIRouter(prefix="/api/v2/chat", tags=["ChatControl"])


def response(request, handler, status=200):
    try:
        return envelope.ok(handler(), status)
    except GraphApiError as exc:
        return envelope.fail(request, exc.status, exc.code, exc.params, exc.message or exc.code)
    except Exception:
        # No exception repr: auth/storage exceptions can carry credentials.
        return envelope.fail(request, 500, "internal_error")


def _bearer(request):
    raw = request.headers.get("Authorization", "") if request is not None else ""
    return raw[7:].strip() if raw.startswith("Bearer ") else None


def _body(raw: bytes):
    if not raw or not raw.strip():
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise GraphApiError(422, "invalid_request", {"errors": [{"field": "body", "reason": "invalid_json"}]})


def _locale(request):
    try:
        return envelope.locale_of(request)
    except Exception:
        return None


@router.post("/flows/{flow_id}/cards")
async def post_cards(flow_id: str, request: Request):
    raw = await request.body()

    def handle():
        scope, group = authz.from_bearer(_bearer(request), flow_id)
        return service.place(group, scope.user_id, _body(raw), _locale(request))

    return await run_in_threadpool(response, request, handle, 201)


@router.post("/flows/{flow_id}/runs")
async def post_run(flow_id: str, request: Request):
    raw = await request.body()

    def handle():
        scope, group = authz.from_bearer(_bearer(request), flow_id)
        return service.start(group, scope.user_id, _body(raw))

    return await run_in_threadpool(response, request, handle, 201)
