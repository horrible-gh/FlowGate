"""RBAC administration API router (admin only).

Endpoints:
  GET    /rbac/roles                          List roles
  GET    /rbac/roles/{role_id}                Role detail
  POST   /rbac/roles                          Create role (admin)
  DELETE /rbac/roles/{role_id}                Delete role (admin, system roles excluded)

  GET    /rbac/permissions                    List permissions
  GET    /rbac/roles/{role_id}/permissions    Permissions for a role
  POST   /rbac/roles/{role_id}/permissions    Add permission to role (admin)
  DELETE /rbac/roles/{role_id}/permissions/{permission_id}
                                              Remove permission from role (admin)

  GET    /rbac/users/{user_id}/roles          User role list
  GET    /rbac/projects/{project_id}/members  Project member role list
  POST   /rbac/users/{user_id}/roles          Assign role to user (admin/manager)
  DELETE /rbac/users/{user_id}/roles/{project_id}
                                              Revoke user role (admin/manager)

  GET    /rbac/projects/{project_id}/my-permissions
                                              Current user's project permission list
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from modules.flow_gate.auth.middleware import get_current_user
from modules.flow_gate.db import roles as db_roles
from modules.flow_gate.db import permissions as db_permissions
from modules.flow_gate.rbac.decorators import require_system_permission
from modules.flow_gate.rbac.permission_service import get_user_permissions
from modules.flow_gate.rbac.role_service import (
    assign_role,
    list_project_members,
    list_user_roles,
    revoke_role,
)

router = APIRouter()


# ── Request schemas ───────────────────────────────────────────────────────────

class RoleCreateRequest(BaseModel):
    role_id: str
    role_name: str
    description: Optional[str] = None


class PermissionAssignRequest(BaseModel):
    permission_id: str


class RoleAssignRequest(BaseModel):
    project_id: str
    role_id: str


# ── Role management ───────────────────────────────────────────────────────────

@router.get("/roles")
def list_roles(user: dict = Depends(get_current_user)):
    """List all roles."""
    return db_roles.list_roles()


@router.get("/roles/{role_id}")
def get_role(role_id: str, user: dict = Depends(get_current_user)):
    """Get role by ID."""
    row = db_roles.get_by_id(role_id)
    if not row:
        raise HTTPException(status_code=404, detail=f"Role not found: {role_id}")
    return row


@router.post("/roles", status_code=201)
def create_role(
    body: RoleCreateRequest,
    _: dict = Depends(require_system_permission("perm_role_assign")),
):
    """Create a role (requires perm_role_assign permission)."""
    try:
        return db_roles.create({
            "role_id": body.role_id,
            "role_name": body.role_name,
            "description": body.description,
            "is_system": 0,
        })
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/roles/{role_id}", status_code=204, response_model=None)
def delete_role(
    role_id: str,
    _: dict = Depends(require_system_permission("perm_role_revoke")),
):
    """Delete a role (system roles cannot be deleted, requires perm_role_revoke permission)."""
    try:
        db_roles.delete(role_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# ── Permission management ─────────────────────────────────────────────────────

@router.get("/permissions")
def list_permissions(user: dict = Depends(get_current_user)):
    """List all permissions."""
    return db_permissions.list_permissions()


@router.get("/roles/{role_id}/permissions")
def list_role_permissions(role_id: str, user: dict = Depends(get_current_user)):
    """List permissions assigned to a role."""
    return db_permissions.list_by_role(role_id)


@router.post("/roles/{role_id}/permissions", status_code=201)
def assign_permission_to_role(
    role_id: str,
    body: PermissionAssignRequest,
    _: dict = Depends(require_system_permission("perm_role_assign")),
):
    """Add a permission to a role."""
    if not db_roles.get_by_id(role_id):
        raise HTTPException(status_code=404, detail=f"Role not found: {role_id}")
    if not db_permissions.get_by_id(body.permission_id):
        raise HTTPException(
            status_code=404,
            detail=f"Permission not found: {body.permission_id}",
        )
    db_permissions.assign_to_role(role_id, body.permission_id)
    return {"role_id": role_id, "permission_id": body.permission_id}


@router.delete("/roles/{role_id}/permissions/{permission_id}", status_code=204, response_model=None)
def revoke_permission_from_role(
    role_id: str,
    permission_id: str,
    _: dict = Depends(require_system_permission("perm_role_revoke")),
):
    """Remove a permission from a role."""
    db_permissions.revoke_from_role(role_id, permission_id)


# ── User role management ──────────────────────────────────────────────────────

@router.get("/users/{user_id}/roles")
def get_user_roles(
    user_id: str,
    current_user: dict = Depends(get_current_user),
):
    """List all project roles for a user.

    Accessible only by the user themselves or an admin/perm_user_read holder.
    """
    if not current_user.get("is_admin") and current_user["user_id"] != user_id:
        perms = get_user_permissions(current_user["user_id"], "__SYSTEM__")
        if "perm_user_read" not in perms:
            raise HTTPException(status_code=403, detail="Permission denied: perm_user_read")
    return list_user_roles(user_id)


@router.get("/projects/{project_id}/members")
def get_project_members(
    project_id: str,
    _: dict = Depends(require_system_permission("perm_user_read")),
):
    """List project member roles."""
    return list_project_members(project_id)


@router.post("/users/{user_id}/roles", status_code=201)
def assign_user_role(
    user_id: str,
    body: RoleAssignRequest,
    current_user: dict = Depends(get_current_user),
):
    """Assign a role to a user.

    Requires admin or perm_role_assign permission.
    """
    if not current_user.get("is_admin"):
        perms = get_user_permissions(current_user["user_id"], body.project_id)
        if "perm_role_assign" not in perms:
            raise HTTPException(status_code=403, detail="Permission denied: perm_role_assign")

    if not db_roles.get_by_id(body.role_id):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid role: {body.role_id}",
        )

    try:
        return assign_role(
            user_id=user_id,
            project_id=body.project_id,
            role_id=body.role_id,
            granted_by=current_user["user_id"],
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/users/{user_id}/roles/{project_id}", status_code=204, response_model=None)
def revoke_user_role(
    user_id: str,
    project_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Revoke a user's role.

    Requires admin or perm_role_revoke permission.
    """
    if not current_user.get("is_admin"):
        perms = get_user_permissions(current_user["user_id"], project_id)
        if "perm_role_revoke" not in perms:
            raise HTTPException(status_code=403, detail="Permission denied: perm_role_revoke")
    revoke_role(user_id, project_id)


# ── Current user permission query ─────────────────────────────────────────────

@router.get("/projects/{project_id}/my-permissions")
def get_my_permissions(
    project_id: str,
    current_user: dict = Depends(get_current_user),
):
    """List permissions of the current user for the given project.

    Called from frontend useAuthStore.loadPermissions() (D011 r1 §6-1).
    Admins are considered to hold all permissions.
    """
    if current_user.get("is_admin"):
        from modules.flow_gate.db import permissions as db_perms
        all_perms = [row["permission_id"] for row in db_perms.list_permissions()]
        return {"project_id": project_id, "permissions": all_perms, "is_admin": True}

    user_id: str = current_user["user_id"]
    perms = get_user_permissions(user_id, project_id)
    return {"project_id": project_id, "permissions": sorted(perms), "is_admin": False}
