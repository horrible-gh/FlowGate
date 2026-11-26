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


def _project_workspace(actor, project_id, action):
    from modules.flow_gate.settings import workspace_service as ws

    if not access.can(actor, project_id, action):
        raise ws.WorkspaceError(403, "forbidden")
    if project_id == "__SYSTEM__":
        raise ws.WorkspaceError(403, "forbidden")
    workspace_id = ws.workspace_id_of_project(project_id)
    if not workspace_id:
        raise ws.WorkspaceError(404, "project_not_found")
    return workspace_id


def _write(actor, project_id, operation, subject, prepare_subject, revision, preview):
    from modules.flow_gate.rbac import mutation
    from modules.flow_gate.settings import workspace_service as ws

    def prepare():
        workspace_id = _project_workspace(actor, project_id, "project.member.manage")
        if ws._require(workspace_id)["status"] != "active":
            raise ws.WorkspaceError(409, "workspace_disabled")
        return [project_id], prepare_subject(workspace_id)

    try:
        return mutation.change(actor, operation, f"{project_id}/{subject}", prepare,
                               revision=revision, preview=preview)
    except ws.WorkspaceError as exc:
        raise HTTPException(status_code=exc.status, detail={"code": exc.code}) from None


def change_direct_role(actor, project_id, user_id, role_id, revision, preview):
    from modules.flow_gate.db import workspaces
    from modules.flow_gate.settings import workspace_service as ws

    def prepare(workspace_id):
        if role_id is None:
            return lambda: ws.revoke_direct_project_role(user_id, project_id, actor=actor)
        role = permission_service.fixed_role(role_id)
        if not role:
            raise ws.WorkspaceError(422, "role_invalid")
        if not workspaces.get_member(workspace_id, user_id):
            raise ws.WorkspaceError(403, "not_workspace_member")
        return lambda: ws.assign_direct_project_role(user_id, project_id, role, actor=actor)

    op = "project_user_revoke" if role_id is None else "project_user_assign"
    return _write(actor, project_id, op, user_id, prepare, revision, preview)


@router.post("/users/{user_id}/roles", status_code=201)
def assign_user_role(user_id: str, body: RoleAssignRequest, revision: int | None = None,
                     preview: bool = False, current_user: dict = Depends(get_current_user)):
    return change_direct_role(current_user, body.project_id, user_id, body.role_id, revision, preview)


@router.delete("/users/{user_id}/roles/{project_id}")
def revoke_user_role(user_id: str, project_id: str, revision: int | None = None,
                     preview: bool = False, current_user: dict = Depends(get_current_user)):
    return change_direct_role(current_user, project_id, user_id, None, revision, preview)


class TeamRoleRequest(BaseModel):
    role_id: str


def _team_role(actor, project_id, team_id, role_id, revision, preview):
    from modules.flow_gate.rbac import role_service as roles
    from modules.flow_gate.settings.routers.teams import _team
    from modules.flow_gate.settings.workspace_service import WorkspaceError

    def prepare(workspace_id):
        team = _team(workspace_id, team_id)
        if role_id is None:
            return lambda: roles.revoke_team_role(team_id, project_id)
        if not team["is_active"]:
            raise WorkspaceError(409, "team_disabled")
        role = permission_service.fixed_role(role_id)
        if not role:
            raise WorkspaceError(422, "role_invalid")
        return lambda: roles.assign_team_role(team_id, project_id, role, actor["user_id"])

    op = "project_team_revoke" if role_id is None else "project_team_assign"
    return _write(actor, project_id, op, team_id, prepare, revision, preview)


@router.get("/projects/{project_id}/member-candidates")
def project_member_candidates(project_id: str, actor: dict = Depends(get_current_user)):
    from modules.flow_gate.db import workspaces
    from modules.flow_gate.db.connection import get_store
    from modules.flow_gate.settings.workspace_service import WorkspaceError

    try:
        workspace_id = _project_workspace(actor, project_id, "project.member.manage")
    except WorkspaceError as exc:
        raise HTTPException(status_code=exc.status, detail={"code": exc.code}) from None
    users = [{"user_id": row["user_id"], "username": row["username"], "role": row["role"]}
             for row in workspaces.list_members(workspace_id) if row["is_active"]]
    teams = get_store()._fetch_all(
        "SELECT team_id, name FROM teams WHERE workspace_id = ? AND is_active = 1 ORDER BY name", [workspace_id],
    )
    return {"users": users, "teams": teams}


@router.get("/projects/{project_id}/team-roles")
def project_team_roles(project_id: str, actor: dict = Depends(get_current_user)):
    from modules.flow_gate.db.connection import get_store
    from modules.flow_gate.settings.workspace_service import WorkspaceError

    try:
        _project_workspace(actor, project_id, "project.member.read")
    except WorkspaceError as exc:
        raise HTTPException(status_code=exc.status, detail={"code": exc.code}) from None
    return {"teams": get_store()._fetch_all(
        "SELECT t.team_id, t.name, t.is_active, r.role_id FROM team_project_roles r"
        " JOIN teams t ON t.team_id = r.team_id WHERE r.project_id = ? ORDER BY t.name", [project_id])}


@router.put("/projects/{project_id}/team-roles/{team_id}")
def assign_team_role(project_id: str, team_id: str, body: TeamRoleRequest, revision: int | None = None,
                     preview: bool = False, actor: dict = Depends(get_current_user)):
    return _team_role(actor, project_id, team_id, body.role_id, revision, preview)


@router.delete("/projects/{project_id}/team-roles/{team_id}")
def revoke_team_role(project_id: str, team_id: str, revision: int | None = None,
                     preview: bool = False, actor: dict = Depends(get_current_user)):
    return _team_role(actor, project_id, team_id, None, revision, preview)


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
