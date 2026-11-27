"""Workspace teams and their project grants."""
from __future__ import annotations

from fastapi import Depends, Query
from pydantic import BaseModel, Field

from modules.flow_gate.auth.middleware import get_current_user
from modules.flow_gate.db import users, workspaces
from modules.flow_gate.db.connection import get_store, now_iso
from modules.flow_gate.rbac import access, mutation, permission_service, role_service
from modules.flow_gate.settings import workspace_service as ws


class TeamName(BaseModel):
    name: str = Field(min_length=1, max_length=120)


def _workspace(actor, workspace_id, permission="workspace.team.manage"):
    workspace = ws._require(workspace_id)
    ws._check(access.can_workspace(actor, workspace, permission))
    return workspace


def _team(workspace_id, team_id):
    team = role_service.get_team(team_id)
    if not team:
        raise ws.WorkspaceError(404, "team_not_found")
    if team["workspace_id"] != workspace_id:
        raise ws.WorkspaceError(403, "team_outside_workspace")
    return team


def team_grants(team_id):
    return get_store()._fetch_all(
        "SELECT project_id, role_id FROM team_project_roles WHERE team_id = ? ORDER BY project_id",
        [team_id],
    )


def _named(workspace_id, name, team_id=None):
    name = name.strip()
    if not name:
        raise ws.WorkspaceError(422, "name_required")
    row = get_store()._fetch_one("SELECT team_id FROM teams WHERE workspace_id = ? AND name = ?",
                                 [workspace_id, name])
    if row and row["team_id"] != team_id:
        raise ws.WorkspaceError(409, "team_name_exists")
    return name


def _mutate(actor, workspace_id, operation, target, prepare, revision, preview):
    def check():
        _workspace(actor, workspace_id)
        return prepare()

    return mutation.change(actor, operation, target, check, revision=revision, preview=preview)


def install(router, run):
    """Add team endpoints to the workspace router."""
    root = "/workspaces/{workspace_id}/teams"

    @router.get(root)
    def list_teams(workspace_id: str, actor: dict = Depends(get_current_user)):
        def read():
            _workspace(actor, workspace_id)
            return {"teams": get_store()._fetch_all(
                "SELECT * FROM teams WHERE workspace_id = ? ORDER BY name", [workspace_id])}
        return run(read)

    @router.get(root + "/{team_id}")
    def get_team(workspace_id: str, team_id: str, actor: dict = Depends(get_current_user)):
        def read():
            _workspace(actor, workspace_id)
            return {**_team(workspace_id, team_id), "members": role_service.team_member_ids(team_id),
                    "project_roles": team_grants(team_id)}
        return run(read)

    @router.post(root, status_code=201)
    def create(workspace_id: str, body: TeamName, revision: int | None = Query(None),
               preview: bool = Query(False), actor: dict = Depends(get_current_user)):
        def prepare():
            name = _named(workspace_id, body.name)
            return (), lambda: role_service.create_team(workspace_id, name, actor["user_id"])
        return run(lambda: _mutate(actor, workspace_id, "team.create", workspace_id, prepare, revision, preview))

    @router.patch(root + "/{team_id}")
    def rename(workspace_id: str, team_id: str, body: TeamName, revision: int | None = Query(None),
               preview: bool = Query(False), actor: dict = Depends(get_current_user)):
        def prepare():
            _team(workspace_id, team_id)
            name = _named(workspace_id, body.name, team_id)

            def apply():
                get_store()._execute("UPDATE teams SET name = ?, updated_at = ? WHERE team_id = ?",
                                     [name, now_iso(), team_id])
                return role_service.get_team(team_id)
            return (), apply
        return run(lambda: _mutate(actor, workspace_id, "team.rename", team_id, prepare, revision, preview))

    @router.post(root + "/{team_id}/disable")
    def disable(workspace_id: str, team_id: str, revision: int | None = Query(None),
                preview: bool = Query(False), actor: dict = Depends(get_current_user)):
        def prepare():
            _team(workspace_id, team_id)

            def apply():
                store = get_store()
                store._execute("UPDATE teams SET is_active = 0, updated_at = ? WHERE team_id = ?",
                               [now_iso(), team_id])
                permission_service.bump_user_versions(role_service.team_member_ids(team_id), store)
                return role_service.get_team(team_id)
            return [row["project_id"] for row in team_grants(team_id)], apply
        return run(lambda: _mutate(actor, workspace_id, "team.disable", team_id, prepare, revision, preview))

    def member_change(workspace_id, team_id, user_id, add, actor, revision, preview):
        def prepare():
            team = _team(workspace_id, team_id)
            if add and not team["is_active"]:
                raise ws.WorkspaceError(409, "team_disabled")
            if add:
                target = users.get_by_id(user_id)
                if not target or not target["is_active"]:
                    raise ws.WorkspaceError(422, "user_inactive")
                if not workspaces.get_member(workspace_id, user_id):
                    raise ws.WorkspaceError(403, "not_workspace_member")
                apply = lambda: role_service.add_team_member(team_id, user_id, actor["user_id"])
            else:
                apply = lambda: role_service.remove_team_member(team_id, user_id)
            return [row["project_id"] for row in team_grants(team_id)], apply
        op = "team.member.add" if add else "team.member.remove"
        return run(lambda: _mutate(actor, workspace_id, op, f"{team_id}/{user_id}", prepare, revision, preview))

    @router.put(root + "/{team_id}/members/{user_id}")
    def add_member(workspace_id: str, team_id: str, user_id: str, revision: int | None = Query(None),
                   preview: bool = Query(False), actor: dict = Depends(get_current_user)):
        return member_change(workspace_id, team_id, user_id, True, actor, revision, preview)

    @router.delete(root + "/{team_id}/members/{user_id}")
    def remove_member(workspace_id: str, team_id: str, user_id: str, revision: int | None = Query(None),
                      preview: bool = Query(False), actor: dict = Depends(get_current_user)):
        return member_change(workspace_id, team_id, user_id, False, actor, revision, preview)
