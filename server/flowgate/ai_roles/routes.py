"""Authenticated account AI role API (P-1 §2); mounted under the application context."""
from typing import Any
from fastapi import APIRouter, Body, Depends
from flowgate.ai_roles.legacy_adapter import get_current_user
from flowgate.ai_roles import account

router = APIRouter(prefix="/api/v1", tags=["AiSettings"])


@router.get("/me/ai-roles")
def get_my_ai_roles(user=Depends(get_current_user)):
    return account.account_view(user["user_id"])


@router.put("/me/ai-roles")
def put_my_ai_roles(body: Any = Body(default=None), user=Depends(get_current_user)):
    return account.save(user["user_id"], body)
