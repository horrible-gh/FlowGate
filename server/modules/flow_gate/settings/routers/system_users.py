"""System › 사용자 / 보안 API (flowgate.v02.0020, 시안 13 / 14).

GET  /api/v1/system/users                          users with state, 2FA, workspaces, summary
POST /api/v1/system/users                          create (invite link or temporary password)
PUT  /api/v1/system/users/{uid}/admin              system administrator on/off (last-admin guard)
POST /api/v1/system/users/{uid}/disable            disable / cancel invitation (sessions ended)
POST /api/v1/system/users/{uid}/enable             enable again
POST /api/v1/system/users/{uid}/invite             issue a new invitation link
POST /api/v1/system/users/{uid}/password-reset     new temporary password
POST /api/v1/system/users/{uid}/tfa-reset          delete the 2FA registration
GET  /api/v1/system/security                       security policy + 2FA coverage
PUT  /api/v1/system/security                       save part of the policy

Errors answer {"detail": {"code": <reason>}}.
"""
from __future__ import annotations

from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict

from modules.flow_gate.rbac.decorators import require_permission
from modules.flow_gate.settings import security_policy_service as security
from modules.flow_gate.settings import system_user_service as svc

router = APIRouter(prefix="/system", tags=["SystemUsers"])


def _run(call):
    try:
        return call()
    except svc.SystemUserError as exc:
        raise HTTPException(status_code=exc.status, detail={"code": exc.code}) from None


class UserCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    email: str
    start: Literal["invite", "password"] = "invite"
    is_admin: bool = False
    workspace_id: Optional[str] = None


class AdminPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    is_admin: bool


class SecurityPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tfa_scope: Optional[Literal["off", "admin", "all"]] = None
    tfa_grace_days: Optional[Literal[0, 7, 14, 30]] = None
    recovery_codes: Optional[bool] = None
    password_min_length: Optional[Literal[8, 10, 12]] = None
    lock_attempts: Optional[Literal[3, 5, 10]] = None
    lock_minutes: Optional[Literal[5, 15, 60]] = None
    idle_timeout: Optional[Literal["30m", "2h", "8h", "7d"]] = None
    logout_on_tighten: Optional[bool] = None


@router.get("/users")
def list_system_users(user: dict = Depends(require_permission("system.user.read"))):
    return svc.list_users()


@router.post("/users", status_code=201)
def create_system_user(body: UserCreate, user: dict = Depends(require_permission("system.user.create"))):
    return _run(lambda: svc.create_user(user, body.name, body.email, body.start, body.is_admin, body.workspace_id))


@router.put("/users/{user_id}/admin")
def set_system_admin(user_id: str, body: AdminPatch, user: dict = Depends(require_permission("system.user.update"))):
    return _run(lambda: svc.set_admin(user, user_id, body.is_admin))


@router.post("/users/{user_id}/disable")
def disable_system_user(user_id: str, user: dict = Depends(require_permission("system.user.update"))):
    return _run(lambda: svc.disable_user(user, user_id))


@router.post("/users/{user_id}/enable")
def enable_system_user(user_id: str, user: dict = Depends(require_permission("system.user.update"))):
    return _run(lambda: svc.enable_user(user, user_id))


@router.post("/users/{user_id}/invite")
def resend_system_invite(user_id: str, user: dict = Depends(require_permission("system.user.update"))):
    return _run(lambda: svc.resend_invite(user, user_id))


@router.post("/users/{user_id}/password-reset")
def reset_system_password(user_id: str, user: dict = Depends(require_permission("system.user.update"))):
    return _run(lambda: svc.reset_password(user, user_id))


@router.post("/users/{user_id}/tfa-reset")
def reset_system_tfa(user_id: str, user: dict = Depends(require_permission("system.user.update"))):
    return _run(lambda: svc.reset_tfa(user, user_id))


def _security_view() -> dict:
    policy = security.get_policy(use_cache=False)
    return {"policy": policy, "people": security.coverage()}


@router.get("/security")
def get_security(user: dict = Depends(require_permission("system.settings.manage"))):
    return _security_view()


@router.put("/security")
def update_security(body: SecurityPatch, user: dict = Depends(require_permission("system.settings.manage"))):
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    try:
        result = security.update_policy(updates, actor=user.get("user_id"))
    except security.SecurityPolicyError as exc:
        raise HTTPException(status_code=422, detail={"code": exc.code, "field": exc.field}) from None
    return {**_security_view(), "signed_out": result["signed_out"]}
