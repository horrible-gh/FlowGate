"""User administration API router (D018 r1 §B, D-2).

GET    /api/v1/users              — list (admin: all, manager: own projects)
POST   /api/v1/users              — create (admin)
GET    /api/v1/users/{uid}        — detail (admin)
PATCH  /api/v1/users/{uid}        — update (admin)
DELETE /api/v1/users/{uid}        — deactivate (admin)
POST   /api/v1/users/{uid}/totp/reset     — reset TOTP (admin)
POST   /api/v1/users/{uid}/password/reset — force-reset password (admin)
POST   /api/v1/users/{uid}/unlock         — unlock account (admin)
GET    /api/v1/users/{uid}/project-roles
POST   /api/v1/users/{uid}/project-roles
DELETE /api/v1/users/{uid}/project-roles/{project_id}
GET    /api/v1/me/backup-codes    — current user's backup code status
POST   /api/v1/me/backup-codes    — regenerate current user's backup codes
"""
from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from modules.flow_gate.auth.middleware import get_current_user
from modules.flow_gate.rbac.decorators import _has_permission, require_permission
from modules.flow_gate.settings.user_admin_service import (
    assign_project_role,
    create_user,
    deactivate_user,
    get_backup_code_status,
    get_user,
    get_user_project_roles,
    list_users_for_admin,
    list_users_for_manager,
    regenerate_backup_codes,
    reset_password,
    reset_totp,
    revoke_project_role,
    unlock_user,
    update_user,
)

router = APIRouter(tags=["UserAdmin"])


@router.get("/users")
def list_users(
    search: str | None = Query(None),
    role: str | None = Query(None),
    is_active: int | None = Query(None),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    user: dict = Depends(get_current_user),
):
    if _has_permission(user, "system.user.read", None):
        return list_users_for_admin(search=search, role=role, is_active=is_active, page=page, per_page=per_page)
    return list_users_for_manager(manager_id=user["user_id"], page=page, per_page=per_page)


class UserCreate(BaseModel):
    username: str
    email: str
    password: str
    is_active: int = 1
    is_admin: int = 0


@router.post("/users", status_code=201)
def create_user_endpoint(
    body: UserCreate,
    user: dict = Depends(require_permission("system.user.create")),
):
    try:
        return create_user(body.model_dump(), created_by=user.get("user_id"))
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.get("/users/{uid}")
def get_user_endpoint(uid: str, user: dict = Depends(require_permission("system.user.read"))):
    row = get_user(uid)
    if not row:
        raise HTTPException(status_code=404, detail="User not found")
    return row


class UserPatch(BaseModel):
    username: str | None = None
    email: str | None = None
    is_active: int | None = None
    is_admin: int | None = None


@router.patch("/users/{uid}")
def update_user_endpoint(
    uid: str,
    body: UserPatch,
    user: dict = Depends(require_permission("system.user.update")),
):
    row = update_user(uid, {k: v for k, v in body.model_dump().items() if v is not None})
    if not row:
        raise HTTPException(status_code=404, detail="User not found")
    return row


@router.delete("/users/{uid}", status_code=200)
def delete_user_endpoint(uid: str, user: dict = Depends(require_permission("system.user.delete"))):
    row = deactivate_user(uid)
    if not row:
        raise HTTPException(status_code=404, detail="User not found")
    return {"detail": "deactivated", "user": row}


@router.post("/users/{uid}/totp/reset")
def totp_reset(uid: str, user: dict = Depends(require_permission("system.user.update"))):
    if not reset_totp(uid):
        raise HTTPException(status_code=404, detail="User not found")
    return {"detail": "TOTP reset"}


class PasswordResetBody(BaseModel):
    new_password: str | None = None


@router.post("/users/{uid}/password/reset")
def password_reset(
    uid: str,
    body: PasswordResetBody,
    user: dict = Depends(require_permission("system.user.update")),
):
    password = body.new_password or secrets.token_urlsafe(12)
    if not reset_password(uid, password):
        raise HTTPException(status_code=404, detail="User not found")
    return {"detail": "password reset", "new_password": password}


@router.post("/users/{uid}/unlock")
def unlock(uid: str, user: dict = Depends(require_permission("system.user.update"))):
    if not unlock_user(uid):
        raise HTTPException(status_code=404, detail="User not found")
    return {"detail": "unlocked"}


@router.get("/users/{uid}/project-roles")
def get_roles(uid: str, user: dict = Depends(require_permission("system.user.read"))):
    return {"roles": get_user_project_roles(uid)}


class AssignRoleBody(BaseModel):
    project_id: str
    role_id: str


@router.post("/users/{uid}/project-roles", status_code=201)
def assign_role(
    uid: str,
    body: AssignRoleBody,
    user: dict = Depends(require_permission("system.user.assign_role")),
):
    return assign_project_role(uid, body.project_id, body.role_id, granted_by=user.get("user_id"))


@router.delete("/users/{uid}/project-roles/{project_id}", status_code=200)
def revoke_role(
    uid: str,
    project_id: str,
    user: dict = Depends(require_permission("system.user.assign_role")),
):
    revoke_project_role(uid, project_id)
    return {"detail": "revoked"}


@router.get("/me/backup-codes")
def my_backup_codes(user: dict = Depends(get_current_user)):
    return get_backup_code_status(user["user_id"])


@router.post("/me/backup-codes")
def my_regen_backup_codes(user: dict = Depends(get_current_user)):
    codes = regenerate_backup_codes(user["user_id"])
    return {"codes": codes, "warning": "These codes are shown only once. Please save them."}
