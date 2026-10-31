"""Authenticated account AI role API (P-1 §2); mounted under the application context.

flowgate.v02.0113 T#2 rework: ``POST /api/v1/flows/{flow_id}/ai-roles/first-setup`` saves the account's first AI setup and
applies it to the Flow's AI-settings cards in one transaction (``first_setup``). Being a Flow write it answers in the
Graph envelope (P0008 §1.2 / §1.3) with the Flow's user JWT and RBAC write, like the card settings routes.
"""
from typing import Any
from fastapi import APIRouter, Body, Depends, Request
from flowgate.ai_roles.legacy_adapter import get_current_user
from flowgate.ai_roles import account, first_setup
from flowgate.card_settings import legacy_adapter as card_legacy

router = APIRouter(prefix="/api/v1", tags=["AiSettings"])


@router.get("/me/ai-roles")
def get_my_ai_roles(user=Depends(get_current_user)):
    return account.account_view(user["user_id"])


@router.put("/me/ai-roles")
def put_my_ai_roles(body: Any = Body(default=None), user=Depends(get_current_user)):
    return account.save(user["user_id"], body)


@router.post("/flows/{flow_id}/ai-roles/first-setup")
def post_first_ai_setup(flow_id: str, request: Request, body: Any = Body(default=None)):
    def handle() -> dict:
        user_id = card_legacy.authenticate(request)
        group = card_legacy.load_flow(flow_id)
        card_legacy.authorize(user_id, group, "write")
        return first_setup.apply(group, user_id, body)

    return card_legacy.run(request, handle)
