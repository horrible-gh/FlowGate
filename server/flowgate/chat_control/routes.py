"""CLI chat transport of the control tower (stage 1). Authentication runs before the body is parsed.

``GET`` / ``PATCH /api/v2/chat/flows/{flow_id}/nodes/{node_key}/roles`` — the AI roles of a work
card this chat placed (``card_roles``).

``GET /api/v2/chat/flows/{flow_id}/ai-options`` — the AIs (and AI groups with role profiles) a
chat may pick before it places cards (``placement_roles.options``), any mode."""
from __future__ import annotations

import json

from fastapi import APIRouter, Request
from starlette.concurrency import run_in_threadpool

from modules.flow_gate.graph_workflow.api import envelope
from modules.flow_gate.graph_workflow.services.errors import GraphApiError
from modules.flow_gate.services import ai_response_slim

from . import authz, card_roles, placement_roles, service, recovery

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
        caller = authz.caller_from_bearer(_bearer(request), flow_id, write=True)
        return service.place(caller.group, caller.scope.user_id, _body(raw), _locale(request),
                             owner=authz.owner(caller.scope, caller.chat_doc_id))

    return await run_in_threadpool(response, request, handle, 201)


@router.post("/flows/{flow_id}/runs")
async def post_run(flow_id: str, request: Request):
    raw = await request.body()

    def handle():
        scope, group = authz.from_bearer(_bearer(request), flow_id)
        return service.start(group, scope.user_id, _body(raw))

    return await run_in_threadpool(response, request, handle, 201)


@router.get("/flows/{flow_id}/ai-options")
async def get_ai_options(flow_id: str, request: Request):
    def handle():
        caller = authz.caller_from_bearer(_bearer(request), flow_id, write=False)
        # A chat reads this list without its empty fields (same names, same meaning).
        return ai_response_slim.slim_ai_options(
            placement_roles.options(caller.scope.user_id, caller.group["group_id"]))

    return await run_in_threadpool(response, request, handle)


# the AI roles of a card this chat placed (``card_roles``).

@router.get("/flows/{flow_id}/nodes/{node_key}/roles")
async def get_card_roles(flow_id: str, node_key: str, request: Request):
    def handle():
        caller = authz.caller_from_bearer(_bearer(request), flow_id, write=False)
        return ai_response_slim.slim_card_roles(card_roles.view(caller, node_key))

    return await run_in_threadpool(response, request, handle)


@router.patch("/flows/{flow_id}/nodes/{node_key}/roles")
async def patch_card_roles(flow_id: str, node_key: str, request: Request):
    raw = await request.body()

    def handle():
        caller = authz.caller_from_bearer(_bearer(request), flow_id, write=True)
        return card_roles.change(caller, node_key, _body(raw))

    return await run_in_threadpool(response, request, handle)


@router.get("/flows/{flow_id}/recovery-basis")
async def get_recovery_basis(flow_id: str, request: Request):
    def handle():
        caller = authz.caller_from_bearer(_bearer(request), flow_id, write=False)
        return recovery.view(caller)
    return await run_in_threadpool(response, request, handle)


@router.get("/flows/{flow_id}/nodes/{node_key}/basis")
async def get_card_basis(flow_id: str, node_key: str, request: Request):
    def handle():
        caller = authz.caller_from_bearer(_bearer(request), flow_id, write=False)
        return recovery.view(caller, node_key)
    return await run_in_threadpool(response, request, handle)


@router.post("/flows/{flow_id}/nodes/{node_key}/retry")
async def post_card_retry(flow_id: str, node_key: str, request: Request):
    raw = await request.body()
    def handle():
        caller = authz.caller_from_bearer(_bearer(request), flow_id, write=True)
        return recovery.change(caller, _body(raw), node_key)
    return await run_in_threadpool(response, request, handle)


@router.post("/flows/{flow_id}/restart")
async def post_flow_restart(flow_id: str, request: Request):
    raw = await request.body()
    def handle():
        caller = authz.caller_from_bearer(_bearer(request), flow_id, write=True)
        return recovery.change(caller, _body(raw))
    return await run_in_threadpool(response, request, handle)
