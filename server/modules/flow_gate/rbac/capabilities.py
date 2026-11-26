"""Action-level permissions displayed by settings and workflow clients."""
from __future__ import annotations

from modules.flow_gate.db.connection import get_store
from modules.flow_gate.rbac import access, matrix, permission_service as ps


def system(user):
    admin = access.is_admin(user)
    return {
        "role": "system_admin" if admin else None,
        "sources": [{"kind": "system_admin" if admin else "none"}],
        "actions": {action: {"allowed": True} if access.can(user, None, action)
                    else {"allowed": False, "reason": "system_admin_only"}
                    for action in matrix.SYSTEM_PERMISSIONS},
    }


def workspace(user, row):
    role = access.workspace_member_role(row["workspace_id"], user["user_id"])
    admin = access.is_admin(user)
    actions = {}
    for action in matrix.WORKSPACE_PERMISSIONS:
        allowed = access.can_workspace(user, row, action)
        value = {"allowed": allowed}
        if not allowed:
            value["reason"] = ("workspace_disabled" if row.get("status") != "active"
                               else "role_limit" if role else "not_member")
        actions[action] = value
    source = "system_admin" if admin else "workspace_member_role" if role else "none"
    return {"role": role, "sources": [{"kind": source, "role": role}], "actions": actions}


def _team_sources(user_id, project_id, store):
    if not ps._caps(store)["team_status"]:
        return []
    return [
        {"kind": "team", "team_id": row["team_id"], "name": row["name"], "role": row["role_id"]}
        for row in store._fetch_all(
            "SELECT t.team_id, t.name, r.role_id FROM teams t"
            " JOIN team_members m ON m.team_id = t.team_id"
            " JOIN team_project_roles r ON r.team_id = t.team_id"
            " WHERE m.user_id = ? AND r.project_id = ? AND t.is_active = 1 ORDER BY t.team_id",
            [user_id, project_id],
        )
    ]


def project(user, row, document=None):
    store = get_store()
    project_id = row["project_id"]
    detail = ps.role_detail(user["user_id"], project_id)
    role = "role_admin" if access.is_admin(user) else detail["role"]
    grants = [{"kind": kind, "role": value} for kind, value in detail["grants"].items() if kind != "team"]
    for grant in grants:
        if grant["kind"] == "workspace":
            exception = store._fetch_one(
                "SELECT 1 AS present FROM workspace_project_access WHERE project_id = ? AND user_id = ?",
                [project_id, user["user_id"]],
            )
            grant["kind"] = "workspace_exception" if exception else "workspace_default"
    grants.extend(_team_sources(user["user_id"], project_id, store))
    sources = [grant for grant in grants if grant["role"] == role]
    if access.is_admin(user):
        sources.insert(0, {"kind": "system_admin", "role": role})
    if not sources:
        sources = [{"kind": "none"}]
    allowed_actions = (matrix.PROJECT_PERMISSIONS if access.is_admin(user)
                       else ps.get_user_permissions(user["user_id"], project_id))
    actions = {}
    for action in matrix.PROJECT_PERMISSIONS:
        allowed = action in allowed_actions
        reason = "role_limit" if role else "not_member"
        rule = None
        if document is not None and action in ("project.document.update", "project.document.delete"):
            from modules.flow_gate.rbac import policies

            policy = policies.evaluate(user["user_id"], project_id, action, document)
            if action == "project.document.update" and allowed and not policy.allowed:
                allowed, reason, rule = False, "context_rule", "document_editable"
            if action == "project.document.delete" and role:
                allowed = policy.allowed
                if not allowed:
                    reason, rule = "context_rule", "own_draft"
        value = {"allowed": allowed}
        if not allowed:
            value["reason"] = reason
            if rule:
                value["rule"] = rule
            if reason == "role_limit":
                for candidate in ("role_viewer", "role_reviewer", "role_developer", "role_admin"):
                    if action in ps.role_permissions(candidate, store):
                        value["minimum_role"] = candidate
                        break
        actions[action] = value
    return {"role": role, "sources": sources, "grants": grants, "actions": actions}


def memberships(user_id):
    store = get_store()
    teams = []
    if ps._caps(store)["teams"]:
        teams = store._fetch_all(
            "SELECT t.team_id, t.name, t.workspace_id FROM teams t"
            " JOIN team_members m ON m.team_id = t.team_id WHERE m.user_id = ? ORDER BY t.name",
            [user_id],
        )
    roles = ps.project_roles(user_id)
    return {"teams": teams, "projects": [
        {"project_id": project_id, **ps.role_detail(user_id, project_id)} for project_id in sorted(roles)
    ]}
