"""RBAC API router.

The four project roles and their permissions are fixed by the deploy SQL, so roles cannot be
created, deleted or edited here (requirement 5: no role editing screen).

Endpoints:
  GET    /rbac/roles                          List roles
  GET    /rbac/roles/{role_id}                Role detail

  GET    /rbac/permissions                    List permissions
  GET    /rbac/roles/{role_id}/permissions    Permissions for a role

  GET    /rbac/users/{user_id}/roles          User role list (self, or system.user.read)
  GET    /rbac/projects/{project_id}/members  Project member role list (project.member.read)
  POST   /rbac/users/{user_id}/roles          Assign role to user (project.member.manage)
  DELETE /rbac/users/{user_id}/roles/{project_id}
                                              Revoke user role (project.member.manage)

  GET    /rbac/projects/{project_id}/my-permissions
                                              Current user's project permission list

  GET    /rbac/system/my-permissions          Current user's system permission list
                                              (System scope, no project_id; additive, read-only)
"""
from __future__ import annotations


from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from modules.flow_gate.auth.middleware import get_current_user
from modules.flow_gate.db import roles as db_roles
from modules.flow_gate.db import permissions as db_permissions
from modules.flow_gate.rbac import access, permission_service
from modules.flow_gate.rbac.permission_service import get_user_permissions
from modules.flow_gate.rbac.role_service import (
    list_project_members,
    list_user_roles,
)

router = APIRouter()


# ── Request schemas ───────────────────────────────────────────────────────────

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


# ── Permission management ─────────────────────────────────────────────────────

@router.get("/permissions")
def list_permissions(user: dict = Depends(get_current_user)):
    """List all permissions."""
    return db_permissions.list_permissions()


@router.get("/roles/{role_id}/permissions")
def list_role_permissions(role_id: str, user: dict = Depends(get_current_user)):
    """List permissions assigned to a role."""
    return db_permissions.list_by_role(role_id)


# ── User role management ──────────────────────────────────────────────────────

@router.get("/users/{user_id}/roles")
def get_user_roles(
    user_id: str,
    current_user: dict = Depends(get_current_user),
):
    """List all direct project roles for a user.

    Accessible only by the user themselves or a holder of system.user.read (system administrators).
    """
    if current_user["user_id"] != user_id and not access.can(current_user, None, "system.user.read"):
        raise HTTPException(status_code=403, detail="Permission denied: system.user.read")
    return list_user_roles(user_id)


@router.get("/projects/{project_id}/members")
def get_project_members(
    project_id: str,
    current_user: dict = Depends(get_current_user),
):
    """List the project's direct member roles (project.member.read on that project)."""
    if not access.can(current_user, project_id, "project.member.read"):
        raise HTTPException(status_code=403, detail="Permission denied: project.member.read")
    return list_project_members(project_id)


@router.post("/users/{user_id}/roles", status_code=201)
def assign_user_role(
    user_id: str,
    body: RoleAssignRequest,
    current_user: dict = Depends(get_current_user),
):
    """Assign a direct project role to a user.

    Requires project.member.manage on the target project. Only the four fixed roles (or an old
    alias, stored as its fixed role) are project roles: anything else is 422 role_invalid.
    """
    if not access.can(current_user, body.project_id, "project.member.manage"):
        raise HTTPException(status_code=403, detail="Permission denied: project.member.manage")

    if not db_roles.get_by_id(body.role_id):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid role: {body.role_id}",
        )
    if body.project_id != "__SYSTEM__" and permission_service.fixed_role(body.role_id) is None:
        raise HTTPException(status_code=422, detail={"code": "role_invalid"})

    # a project role is granted through its workspace (membership/exception
    # and the role row in one transaction), never around it.
    from modules.flow_gate.settings import workspace_service

    try:
        return workspace_service.assign_direct_project_role(
            user_id, body.project_id, body.role_id, actor=current_user,
        )
    except workspace_service.WorkspaceError as exc:
        raise HTTPException(status_code=exc.status, detail={"code": exc.code}) from None
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/users/{user_id}/roles/{project_id}", status_code=204, response_model=None)
def revoke_user_role(
    user_id: str,
    project_id: str,
    current_user: dict = Depends(get_current_user),
):
    """Revoke a user's direct project role.

    Requires project.member.manage on the target project.
    """
    if not access.can(current_user, project_id, "project.member.manage"):
        raise HTTPException(status_code=403, detail="Permission denied: project.member.manage")
    from modules.flow_gate.settings import workspace_service

    workspace_service.revoke_direct_project_role(user_id, project_id, actor=current_user)


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


@router.get("/system/my-permissions")
def get_my_system_permissions(current_user: dict = Depends(get_current_user)):
    """List the current user's own System-scope permissions (flowgate.default.0623 T0004 §1).

    Additive to the project-scoped /rbac/projects/{project_id}/my-permissions
    above: this one takes no project_id and resolves permission_service's
    internal "__SYSTEM__" project sentinel on the server side only, so the
    frontend never needs to know it exists. System-scope permissions belong to
    system administrators only (a role on __SYSTEM__ grants nothing), so a
    non-admin gets an empty list. Admins are treated as holding every
    permission, matching the project-scoped endpoint's policy.
    """
    if current_user.get("is_admin"):
        all_perms = sorted(row["permission_id"] for row in db_permissions.list_permissions())
        return {"scope": "system", "permissions": all_perms, "is_admin": True}

    perms = get_user_permissions(current_user["user_id"], "__SYSTEM__")
    return {"scope": "system", "permissions": sorted(perms), "is_admin": False}
