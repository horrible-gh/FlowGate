"""Workspace API (flowgate.v02.0020, 시안 11 워크스페이스 / 12 워크스페이스 상세).

GET    /api/v1/workspaces                                   list (system admin: all; others: own)
POST   /api/v1/workspaces                                   create (system admin)
GET    /api/v1/workspaces/{wid}                             detail: members, project access, policy
PATCH  /api/v1/workspaces/{wid}                             rename / enable / disable (owner)
DELETE /api/v1/workspaces/{wid}                             delete an empty workspace (owner)
GET    /api/v1/workspaces/{wid}/member-candidates?q=        users that can be added
POST   /api/v1/workspaces/{wid}/members                     add a member (owner/admin)
PATCH  /api/v1/workspaces/{wid}/members/{uid}               change role admin/member
DELETE /api/v1/workspaces/{wid}/members/{uid}               remove (also leaves its projects)
PUT    /api/v1/workspaces/{wid}/access/defaults             role default project access
PUT    /api/v1/workspaces/{wid}/access/exceptions           per-person exception for one project
PUT    /api/v1/workspaces/{wid}/policy                      AI mode, who may create projects
GET    /api/v1/workspaces/{wid}/policy/resources            AI / Agent that can be allowed
POST   /api/v1/workspaces/{wid}/policy/allowlist            allow one AI / Agent
DELETE /api/v1/workspaces/{wid}/policy/allowlist/{type}/{id} revoke it

Errors answer {"detail": {"code": <reason>}}. Authorization is decided per workspace in
workspace_service (system admin, or the member's workspace role).
"""
from __future__ import annotations

from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict

from modules.flow_gate.auth.middleware import get_current_user
from modules.flow_gate.settings import workspace_service as svc

router = APIRouter(tags=["Workspaces"])

Access = Literal["admin", "dev", "review", "none"]


def _run(call):
    try:
        return call()
    except svc.WorkspaceError as exc:
        raise HTTPException(status_code=exc.status, detail={"code": exc.code}) from None


class WorkspaceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    owner_user_id: Optional[str] = None


class WorkspacePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Optional[str] = None
    status: Optional[Literal["active", "disabled"]] = None


class MemberAdd(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_id: str
    role: Literal["admin", "member"] = "member"


class MemberPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["admin", "member"]


class AccessDefaults(BaseModel):
    model_config = ConfigDict(extra="forbid")
    owner: Optional[Access] = None
    admin: Optional[Access] = None
    member: Optional[Access] = None


class AccessException(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: str
    user_id: str
    access: Literal["default", "admin", "dev", "review", "none"]


class PolicyPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ai_mode: Optional[Literal["all", "allow"]] = None
    project_create: Optional[Literal["owner", "owner_admin", "all"]] = None


class AllowEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    resource_type: Literal["provider", "agent"]
    resource_id: str


@router.get("/workspaces")
def list_workspaces(user: dict = Depends(get_current_user)):
    return _run(lambda: svc.list_workspaces(user))


@router.post("/workspaces", status_code=201)
def create_workspace(body: WorkspaceCreate, user: dict = Depends(get_current_user)):
    return _run(lambda: svc.create_workspace(user, body.name, body.owner_user_id))


@router.get("/workspaces/{workspace_id}")
def get_workspace(workspace_id: str, user: dict = Depends(get_current_user)):
    return _run(lambda: svc.get_detail(user, workspace_id))


@router.patch("/workspaces/{workspace_id}")
def update_workspace(workspace_id: str, body: WorkspacePatch, user: dict = Depends(get_current_user)):
    return _run(lambda: svc.update_workspace(user, workspace_id, name=body.name, status=body.status))


@router.delete("/workspaces/{workspace_id}")
def delete_workspace(workspace_id: str, user: dict = Depends(get_current_user)):
    _run(lambda: svc.delete_workspace(user, workspace_id))
    return {"ok": True, "workspace_id": workspace_id}


@router.get("/workspaces/{workspace_id}/member-candidates")
def member_candidates(workspace_id: str, q: Optional[str] = Query(None), user: dict = Depends(get_current_user)):
    return {"users": _run(lambda: svc.list_member_candidates(user, workspace_id, q))}


@router.post("/workspaces/{workspace_id}/members", status_code=201)
def add_member(workspace_id: str, body: MemberAdd, user: dict = Depends(get_current_user)):
    return _run(lambda: svc.add_member(user, workspace_id, body.user_id, body.role))


@router.patch("/workspaces/{workspace_id}/members/{member_user_id}")
def change_member(workspace_id: str, member_user_id: str, body: MemberPatch,
                  user: dict = Depends(get_current_user)):
    return _run(lambda: svc.change_member_role(user, workspace_id, member_user_id, body.role))


@router.delete("/workspaces/{workspace_id}/members/{member_user_id}")
def remove_member(workspace_id: str, member_user_id: str, user: dict = Depends(get_current_user)):
    return _run(lambda: svc.remove_member(user, workspace_id, member_user_id))


@router.put("/workspaces/{workspace_id}/access/defaults")
def set_access_defaults(workspace_id: str, body: AccessDefaults, user: dict = Depends(get_current_user)):
    defaults = {k: v for k, v in body.model_dump().items() if v is not None}
    return _run(lambda: svc.set_default_access(user, workspace_id, defaults))


@router.put("/workspaces/{workspace_id}/access/exceptions")
def set_access_exception(workspace_id: str, body: AccessException, user: dict = Depends(get_current_user)):
    return _run(lambda: svc.set_project_access(user, workspace_id, body.project_id, body.user_id, body.access))


@router.put("/workspaces/{workspace_id}/policy")
def set_policy(workspace_id: str, body: PolicyPatch, user: dict = Depends(get_current_user)):
    return _run(lambda: svc.set_policy(user, workspace_id, ai_mode=body.ai_mode, project_create=body.project_create))


@router.get("/workspaces/{workspace_id}/policy/resources")
def policy_resources(workspace_id: str, user: dict = Depends(get_current_user)):
    return _run(lambda: svc.list_policy_resources(user, workspace_id))


@router.post("/workspaces/{workspace_id}/policy/allowlist")
def allow_resource(workspace_id: str, body: AllowEntry, user: dict = Depends(get_current_user)):
    return _run(lambda: svc.allow_resource(user, workspace_id, body.resource_type, body.resource_id))


@router.delete("/workspaces/{workspace_id}/policy/allowlist/{resource_type}/{resource_id}")
def revoke_resource(workspace_id: str, resource_type: str, resource_id: str,
                    user: dict = Depends(get_current_user)):
    return _run(lambda: svc.revoke_resource(user, workspace_id, resource_type, resource_id))
