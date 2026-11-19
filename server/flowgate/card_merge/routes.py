"""HTTP adapter of the card merge (flowgate.v02test.0042). Mounted like the card memos routes (same envelope, user JWT
only — a worker token is refused, so no AI presses ``merge`` / ``discard_rerun``).

    GET  /api/v1/flows/{flow_id}/nodes/{node_key}/merge           the card's merge state (``service.view``)
    POST /api/v1/flows/{flow_id}/nodes/{node_key}/merge/actions   a person's choice (``service.act``), then the state
    GET  /api/v1/merge-items                                      the cards waiting on their merge, every readable Flow

Read: the document read permission; a choice: the document update permission (as a card memo's write) on a writable
Flow.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Request

from flowgate.card_memos import legacy_adapter as legacy
from flowgate.card_settings import repository as card_settings
from modules.flow_gate.rbac.permission_service import has_permission

from . import service

router = APIRouter(prefix="/api/v1", tags=["CardMerge"])


def _card(flow_id: str, node_key: str) -> None:
    if card_settings.card_node(flow_id, node_key) is None:
        raise legacy.GraphApiError(404, "node_not_found", {"flow_id": flow_id, "node_key": node_key},
                                   message="The card does not exist in this flow.")


@router.get("/flows/{flow_id}/nodes/{node_key}/merge")
def get_card_merge(flow_id: str, node_key: str, request: Request):
    def handle() -> dict:
        user_id = legacy.authenticate(request)
        group = legacy.load_flow(flow_id)
        legacy.authorize(user_id, group, "read")
        _card(flow_id, node_key)
        return service.view(flow_id, node_key)

    return legacy.run(request, handle)


@router.post("/flows/{flow_id}/nodes/{node_key}/merge/actions")
def post_card_merge_action(flow_id: str, node_key: str, request: Request, body: Any = Body(default=None)):
    def handle() -> dict:
        user_id = legacy.authenticate(request)
        group = legacy.load_flow(flow_id)
        legacy.authorize(user_id, group, "update")
        legacy.require_writable(flow_id)
        _card(flow_id, node_key)

        def recheck() -> None:
            legacy.authorize(user_id, group, "update")
            legacy.require_writable(flow_id)

        return service.act(flow_id, node_key, user_id, body, request=request, recheck=recheck)

    return legacy.run(request, handle)


@router.get("/merge-items")
def get_merge_items(request: Request):
    def handle() -> dict:
        user_id = legacy.authenticate(request)
        return {"items": service.merge_items(
            user_id, lambda uid, project_id: has_permission(uid, project_id, legacy.PERMISSIONS["read"]))}

    return legacy.run(request, handle)
