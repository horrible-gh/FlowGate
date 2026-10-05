"""Workspace service (flowgate.v02.0020, 시안 11 워크스페이스 / 12 워크스페이스 상세).

A workspace is the membership / project-access / policy boundary above projects:

* Membership: owner (exactly one), admin, member. Users stay system-wide identities.
* Project access: each workspace role has a default access ('admin' Project Admin, 'dev'
  Developer, 'review' Reviewer, 'none') and a member may carry a per-person exception for one
  project. The effective access is materialised into `user_project_roles`, which is still the
  only table every permission check reads (rbac.permission_service) — the workspace decides which
  rows exist, it never adds a second access path.
* Policy: which AI / Agent the workspace's projects may use ('off' = no extra limit,
  'allowlist' = approved entries only) and who may create a project there.

A disabled workspace keeps its members, defaults and exceptions but materialises 'none' for
everyone, so re-enabling restores exactly the previous access.
"""
from __future__ import annotations

import re
import uuid
from typing import Iterable, Optional

from modules.flow_gate.db import connection as _connection
from modules.flow_gate.db import workspaces as _db
from modules.flow_gate.rbac import role_service

ROLES = ("owner", "admin", "member")
MEMBER_ROLES = ("admin", "member")
ACCESS_LEVELS = ("admin", "dev", "review", "none")
PROJECT_CREATE_POLICIES = ("owner", "owner_admin", "all")
AI_MODES = {"all": "off", "allow": "allowlist"}
RESOURCE_TYPES = ("provider", "agent")
NAME_MAX = 100

# Project access level -> the project role materialised in user_project_roles.
ACCESS_TO_ROLE = {"admin": "role_manager", "dev": "role_worker", "review": "role_viewer"}
# Any existing project role read back as an access level (role_admin is a legacy project grant).
ROLE_TO_ACCESS = {"role_admin": "admin", "role_manager": "admin", "role_worker": "dev", "role_viewer": "review"}


class WorkspaceError(Exception):
    """A rejected workspace operation; `code` is the stable client-facing reason."""

    def __init__(self, status: int, code: str, message: str = ""):
        super().__init__(message or code)
        self.status = status
        self.code = code


def _store():
    return _connection.get_store()


def _is_sys_admin(user: Optional[dict]) -> bool:
    return bool(user and user.get("is_admin"))


def user_state(row: dict) -> str:
    """'disabled' / 'invited' (has a pending invitation and never signed in) / 'active'."""
    if not row.get("is_active"):
        return "disabled"
    if row.get("invited_at") and not row.get("last_login_at"):
        return "invited"
    return "active"


def _normalize_name(name: Optional[str]) -> tuple[str, str]:
    value = re.sub(r"\s+", " ", (name or "").strip())
    if not value:
        raise WorkspaceError(422, "name_required")
    if len(value) > NAME_MAX:
        raise WorkspaceError(422, "name_too_long")
    return value, value.lower()


# ── default workspace / project mapping ──────────────────────────────────────

def ensure_default_workspace() -> dict:
    """The default workspace, created when missing; an ownerless default gets the oldest active
    system administrator as owner (a fresh install runs migration 136 before any user exists)."""
    ws = _db.get_default()
    if ws is None:
        ws = _db.get(_db.DEFAULT_WORKSPACE_ID)
        if ws is None:
            ws = _db.create({
                "workspace_id": _db.DEFAULT_WORKSPACE_ID, "name": "FlowGate Workspace",
                "name_key": "flowgate workspace", "is_default": 1, "project_create_policy": "all",
            })
        else:
            ws = _db.update(ws["workspace_id"], {"is_default": 1})
    if not _db.owners().get(ws["workspace_id"]):
        admin = _store()._fetch_one(
            "SELECT user_id FROM users WHERE is_admin = 1 AND is_active = 1 ORDER BY created_at, user_id LIMIT 1"
        )
        if admin:
            existing = _db.get_member(ws["workspace_id"], admin["user_id"])
            if existing:
                _db.set_member_role(ws["workspace_id"], admin["user_id"], "owner")
            else:
                _db.add_member(ws["workspace_id"], admin["user_id"], "owner", None)
    return ws


def workspace_id_of_project(project_id: str) -> Optional[str]:
    row = _store()._fetch_one("SELECT workspace_id FROM projects WHERE project_id = ?", [project_id])
    if row is None or project_id == _db.SYSTEM_PROJECT:
        return None
    return row.get("workspace_id") or ensure_default_workspace()["workspace_id"]


def _projects_of(ws: dict) -> list[dict]:
    return _db.list_projects(ws["workspace_id"], include_unassigned=bool(ws.get("is_default")))


# ── authorization ────────────────────────────────────────────────────────────

def _require(workspace_id: str) -> dict:
    ws = _db.get(workspace_id)
    if not ws:
        raise WorkspaceError(404, "workspace_not_found")
    return ws


def member_role(workspace_id: str, user_id: Optional[str]) -> Optional[str]:
    if not user_id:
        return None
    row = _db.get_member(workspace_id, user_id)
    return row["role"] if row else None


def can_view(user: dict, ws: dict) -> bool:
    return _is_sys_admin(user) or member_role(ws["workspace_id"], user.get("user_id")) is not None


def can_manage(user: dict, ws: dict) -> bool:
    """Members, project access and policy: system admin or workspace owner/admin."""
    return _is_sys_admin(user) or member_role(ws["workspace_id"], user.get("user_id")) in ("owner", "admin")


def can_admin(user: dict, ws: dict) -> bool:
    """Rename / disable / delete: system admin or the workspace owner."""
    return _is_sys_admin(user) or member_role(ws["workspace_id"], user.get("user_id")) == "owner"


def _check(allowed: bool) -> None:
    if not allowed:
        raise WorkspaceError(403, "forbidden")


def can_create_project(user: dict, workspace_id: str) -> bool:
    ws = _db.get(workspace_id)
    if not ws or ws["status"] != "active":
        return False
    if _is_sys_admin(user):
        return True
    role = member_role(workspace_id, user.get("user_id"))
    policy = ws.get("project_create_policy") or "owner_admin"
    if policy == "owner":
        return role == "owner"
    if policy == "owner_admin":
        return role in ("owner", "admin")
    return role is not None


# ── effective access + materialisation ───────────────────────────────────────

def _default_access(ws: dict, role: str) -> str:
    return ws.get(f"default_access_{role}") or "none"


def _effective(ws: dict, role: str, exception: Optional[str]) -> str:
    return exception or _default_access(ws, role)


def sync(workspace_id: str, user_ids: Optional[Iterable[str]] = None,
         project_ids: Optional[Iterable[str]] = None, actor: Optional[str] = None) -> None:
    """Make user_project_roles match the effective access of the given members/projects.

    A row whose role already reads as the wanted access is left untouched (a legacy role_admin
    project grant stays role_admin), so a sync never shifts access it does not have to.
    """
    ws = _db.get(workspace_id)
    if not ws:
        return
    members = {m["user_id"]: m["role"] for m in _db.list_members(workspace_id)}
    targets = [uid for uid in (user_ids if user_ids is not None else members) if uid in members]
    wanted_projects = set(project_ids) if project_ids is not None else None
    projects = [p["project_id"] for p in _projects_of(ws)
                if wanted_projects is None or p["project_id"] in wanted_projects]
    exceptions = {(e["project_id"], e["user_id"]): e["access"] for e in _db.list_exceptions(workspace_id)}
    disabled = ws.get("status") != "active"
    for project_id in projects:
        for uid in targets:
            desired = "none" if disabled else _effective(ws, members[uid], exceptions.get((project_id, uid)))
            current = role_service.get_user_role(uid, project_id)
            if desired == "none":
                if current:
                    role_service.revoke_role(uid, project_id)
            elif not current or ROLE_TO_ACCESS.get(current["role_id"]) != desired:
                role_service.assign_role(uid, project_id, ACCESS_TO_ROLE[desired], granted_by=actor)


def _revoke_workspace_roles(ws: dict, user_id: str) -> None:
    for project in _projects_of(ws):
        if role_service.get_user_role(user_id, project["project_id"]):
            role_service.revoke_role(user_id, project["project_id"])


# ── views ────────────────────────────────────────────────────────────────────

def _summary(ws: dict, user: dict, member_counts: dict, project_counts: dict, owners: dict) -> dict:
    owner = owners.get(ws["workspace_id"])
    projects = project_counts.get(ws["workspace_id"], 0)
    if ws.get("is_default"):
        projects += project_counts.get(None, 0)
    return {
        "workspace_id": ws["workspace_id"],
        "name": ws["name"],
        "status": ws["status"],
        "is_default": bool(ws.get("is_default")),
        "member_count": member_counts.get(ws["workspace_id"], 0),
        "project_count": projects,
        "ai_policy_mode": ws.get("ai_policy_mode") or "off",
        "owner": {"user_id": owner["user_id"], "username": owner["username"], "email": owner.get("email")}
        if owner else None,
        "my_role": member_role(ws["workspace_id"], user.get("user_id")),
        "can_manage": can_manage(user, ws),
        "can_admin": can_admin(user, ws),
    }


def list_workspaces(user: dict) -> dict:
    ensure_default_workspace()
    member_counts, project_counts, owners = _db.member_counts(), _db.project_counts(), _db.owners()
    items = [
        _summary(ws, user, member_counts, project_counts, owners)
        for ws in _db.list_all() if can_view(user, ws)
    ]
    return {"workspaces": items, "can_create": _is_sys_admin(user)}


def get_detail(user: dict, workspace_id: str) -> dict:
    ensure_default_workspace()
    ws = _require(workspace_id)
    _check(can_view(user, ws))
    summary = _summary(ws, user, _db.member_counts(), _db.project_counts(), _db.owners())
    projects = _projects_of(ws)
    active_projects = [p["project_id"] for p in projects if p.get("is_active", 1)]
    exceptions = _db.list_exceptions(workspace_id)
    exc_map = {(e["project_id"], e["user_id"]): e["access"] for e in exceptions}
    members = []
    for row in _db.list_members(workspace_id):
        accessible = sum(
            1 for pid in active_projects
            if _effective(ws, row["role"], exc_map.get((pid, row["user_id"]))) != "none"
        )
        members.append({
            "user_id": row["user_id"],
            "username": row["username"],
            "email": row.get("email"),
            "role": row["role"],
            "state": user_state(row),
            "is_system_admin": bool(row.get("is_admin")),
            "last_login_at": row.get("last_login_at"),
            "joined_at": row.get("joined_at"),
            "project_count": 0 if ws["status"] != "active" else accessible,
        })
    return {
        **summary,
        "members": members,
        "projects": [
            {"project_id": p["project_id"], "project_name": p["project_name"], "is_active": bool(p.get("is_active", 1))}
            for p in projects
        ],
        "active_project_count": len(active_projects),
        "access": {
            "defaults": {role: _default_access(ws, role) for role in ROLES},
            "exceptions": [
                {"project_id": e["project_id"], "user_id": e["user_id"], "access": e["access"]}
                for e in exceptions
            ],
        },
        "policy": _policy_view(ws),
    }


def _policy_view(ws: dict) -> dict:
    names = _resource_names()
    entries = []
    for entry in _db.list_allowlist(ws["workspace_id"]):
        if entry["status"] != "approved":
            continue
        key = (entry["resource_type"], entry["resource_id"])
        entries.append({
            "resource_type": entry["resource_type"],
            "resource_id": entry["resource_id"],
            "name": names.get(key, {}).get("name") or entry["resource_id"],
            "available": key in names,
            "status": entry["status"],
            "source": entry["source"],
        })
    return {
        "ai_mode": "allow" if ws.get("ai_policy_mode") == "allowlist" else "all",
        "allowlist": entries,
        "project_create": ws.get("project_create_policy") or "owner_admin",
    }


# ── workspace lifecycle ──────────────────────────────────────────────────────

def create_workspace(user: dict, name: str, owner_user_id: Optional[str]) -> dict:
    _check(_is_sys_admin(user))
    display, key = _normalize_name(name)
    if _db.get_by_name_key(key):
        raise WorkspaceError(409, "duplicate_name")
    owner_id = owner_user_id or user.get("user_id")
    owner = _store()._fetch_one("SELECT user_id, is_active FROM users WHERE user_id = ?", [owner_id])
    if not owner or not owner.get("is_active"):
        raise WorkspaceError(422, "owner_invalid")
    workspace_id = f"ws_{uuid.uuid4().hex[:12]}"
    with _store().transaction():
        _db.create({"workspace_id": workspace_id, "name": display, "name_key": key,
                    "created_by": user.get("user_id")})
        _db.add_member(workspace_id, owner_id, "owner", user.get("user_id"))
    return get_detail(user, workspace_id)


def update_workspace(user: dict, workspace_id: str, name: Optional[str] = None,
                     status: Optional[str] = None) -> dict:
    ws = _require(workspace_id)
    _check(can_admin(user, ws))
    updates: dict = {}
    if name is not None:
        display, key = _normalize_name(name)
        clash = _db.get_by_name_key(key)
        if clash and clash["workspace_id"] != workspace_id:
            raise WorkspaceError(409, "duplicate_name")
        updates.update(name=display, name_key=key)
    if status is not None:
        if status not in ("active", "disabled"):
            raise WorkspaceError(422, "status_invalid")
        if status == "disabled" and ws.get("is_default"):
            raise WorkspaceError(409, "default_workspace_locked")
        updates["status"] = status
    with _store().transaction():
        _db.update(workspace_id, updates)
        if "status" in updates and updates["status"] != ws["status"]:
            sync(workspace_id, actor=user.get("user_id"))
    return get_detail(user, workspace_id)


def delete_workspace(user: dict, workspace_id: str) -> None:
    ws = _require(workspace_id)
    _check(can_admin(user, ws))
    if ws.get("is_default"):
        raise WorkspaceError(409, "default_workspace_locked")
    if _projects_of(ws):
        raise WorkspaceError(409, "workspace_not_empty")
    if any(m["role"] != "owner" for m in _db.list_members(workspace_id)):
        raise WorkspaceError(409, "workspace_not_empty")
    _db.delete(workspace_id)


# ── members ──────────────────────────────────────────────────────────────────

def list_member_candidates(user: dict, workspace_id: str, query: Optional[str] = None) -> list[dict]:
    ws = _require(workspace_id)
    _check(can_manage(user, ws))
    sql = (
        "SELECT user_id, username, email, is_active, invited_at, last_login_at FROM users u "
        "WHERE u.is_active = 1 AND NOT EXISTS (SELECT 1 FROM workspace_members m "
        "WHERE m.workspace_id = ? AND m.user_id = u.user_id)"
    )
    params: list = [workspace_id]
    if query:
        sql += " AND (u.username LIKE ? OR u.email LIKE ?)"
        params += [f"%{query}%", f"%{query}%"]
    sql += " ORDER BY u.username LIMIT 50"
    return [
        {"user_id": r["user_id"], "username": r["username"], "email": r.get("email"), "state": user_state(r)}
        for r in _store()._fetch_all(sql, params)
    ]


def add_member(user: dict, workspace_id: str, target_user_id: str, role: str) -> dict:
    ws = _require(workspace_id)
    _check(can_manage(user, ws))
    if role not in MEMBER_ROLES:
        raise WorkspaceError(422, "role_invalid")
    target = _store()._fetch_one("SELECT user_id, is_active FROM users WHERE user_id = ?", [target_user_id])
    if not target:
        raise WorkspaceError(404, "user_not_found")
    if not target.get("is_active"):
        raise WorkspaceError(422, "user_disabled")
    if _db.get_member(workspace_id, target_user_id):
        raise WorkspaceError(409, "already_member")
    with _store().transaction():
        _db.add_member(workspace_id, target_user_id, role, user.get("user_id"))
        sync(workspace_id, [target_user_id], actor=user.get("user_id"))
    return get_detail(user, workspace_id)


def change_member_role(user: dict, workspace_id: str, target_user_id: str, role: str) -> dict:
    ws = _require(workspace_id)
    _check(can_manage(user, ws))
    if role not in MEMBER_ROLES:
        raise WorkspaceError(422, "role_invalid")
    current = _db.get_member(workspace_id, target_user_id)
    if not current:
        raise WorkspaceError(404, "member_not_found")
    if current["role"] == "owner":
        raise WorkspaceError(409, "owner_locked")
    with _store().transaction():
        _db.set_member_role(workspace_id, target_user_id, role)
        sync(workspace_id, [target_user_id], actor=user.get("user_id"))
    return get_detail(user, workspace_id)


def remove_member(user: dict, workspace_id: str, target_user_id: str) -> dict:
    """Leaving a workspace also leaves every project of it (R0001 §3.1)."""
    ws = _require(workspace_id)
    _check(can_manage(user, ws))
    current = _db.get_member(workspace_id, target_user_id)
    if not current:
        raise WorkspaceError(404, "member_not_found")
    if current["role"] == "owner":
        raise WorkspaceError(409, "owner_locked")
    with _store().transaction():
        _db.clear_member_exceptions(workspace_id, target_user_id)
        _revoke_workspace_roles(ws, target_user_id)
        _db.remove_member(workspace_id, target_user_id)
    return get_detail(user, workspace_id)


def add_user_to_workspace(workspace_id: str, user_id: str, role: str, actor: Optional[str]) -> None:
    """System › 사용자 만들기 with a workspace: the creator is a system administrator."""
    ws = _require(workspace_id)
    if role not in MEMBER_ROLES:
        raise WorkspaceError(422, "role_invalid")
    if not _db.get_member(workspace_id, user_id):
        _db.add_member(workspace_id, user_id, role, actor)
        sync(ws["workspace_id"], [user_id], actor=actor)


# ── project access ───────────────────────────────────────────────────────────

def set_default_access(user: dict, workspace_id: str, defaults: dict) -> dict:
    ws = _require(workspace_id)
    _check(can_manage(user, ws))
    updates = {}
    for role, access in (defaults or {}).items():
        if role not in ROLES:
            raise WorkspaceError(422, "role_invalid")
        if access not in ACCESS_LEVELS:
            raise WorkspaceError(422, "access_invalid")
        updates[f"default_access_{role}"] = access
    with _store().transaction():
        _db.update(workspace_id, updates)
        sync(workspace_id, actor=user.get("user_id"))
    return get_detail(user, workspace_id)


def set_project_access(user: dict, workspace_id: str, project_id: str, target_user_id: str,
                       access: str) -> dict:
    """access 'default' clears the person's exception for that project."""
    ws = _require(workspace_id)
    _check(can_manage(user, ws))
    if access != "default" and access not in ACCESS_LEVELS:
        raise WorkspaceError(422, "access_invalid")
    if project_id not in {p["project_id"] for p in _projects_of(ws)}:
        raise WorkspaceError(404, "project_not_found")
    if not _db.get_member(workspace_id, target_user_id):
        raise WorkspaceError(404, "member_not_found")
    with _store().transaction():
        if access == "default":
            _db.clear_exception(project_id, target_user_id)
        else:
            _db.set_exception(workspace_id, project_id, target_user_id, access, user.get("user_id"))
        sync(workspace_id, [target_user_id], [project_id], actor=user.get("user_id"))
    return get_detail(user, workspace_id)


def record_direct_assignment(user_id: str, project_id: str, role_id: Optional[str],
                             actor: Optional[str] = None, *, may_add_member: bool = True) -> None:
    """Keep the workspace model in step with a project role granted/revoked through the per-project
    role APIs, so the next sync does not undo it. Runs inside the caller's transaction.

    A grant to someone outside the workspace makes them a Member (a project member is a workspace
    member, R0001 §3.1) with 'none' on every other project of it, so the grant opens exactly that
    one project. A disabled workspace takes no grant, and a role that is not one of the fixed
    project roles cannot be expressed as workspace access."""
    if project_id == _db.SYSTEM_PROJECT:
        return
    workspace_id = workspace_id_of_project(project_id)
    if not workspace_id:
        return
    ws = _db.get(workspace_id)
    member = _db.get_member(workspace_id, user_id)
    if role_id is None:
        if not member:
            return
        if _default_access(ws, member["role"]) == "none":
            _db.clear_exception(project_id, user_id)
        else:
            _db.set_exception(workspace_id, project_id, user_id, "none", actor)
        return
    access = ROLE_TO_ACCESS.get(role_id)
    if access is None:
        raise WorkspaceError(422, "role_invalid")
    if ws.get("status") != "active":
        raise WorkspaceError(409, "workspace_disabled")
    if not member:
        if not may_add_member:
            raise WorkspaceError(403, "not_workspace_member")
        _db.add_member(workspace_id, user_id, "member", actor)
        member = {"role": "member"}
        if _default_access(ws, "member") != "none":
            for project in _projects_of(ws):
                if project["project_id"] != project_id:
                    _db.set_exception(workspace_id, project["project_id"], user_id, "none", actor)
    if _default_access(ws, member["role"]) == access:
        _db.clear_exception(project_id, user_id)
    else:
        _db.set_exception(workspace_id, project_id, user_id, access, actor)


def assign_direct_project_role(user_id: str, project_id: str, role_id: str,
                               actor: Optional[dict] = None, system_grant: bool = False) -> dict:
    """The one path for a project role granted through a per-project role API (설정 › 사용자,
    /rbac/users/{id}/roles): workspace membership/exception, the role row and its materialisation
    commit together. Pulling an outsider into the workspace needs a system-level grant
    (system_grant / system admin) or the workspace's owner/admin."""
    actor_id = (actor or {}).get("user_id")
    with _store().transaction():
        workspace_id = workspace_id_of_project(project_id)
        if workspace_id:
            ws = _db.get(workspace_id)
            may_add = system_grant or _is_sys_admin(actor) or can_manage(actor or {}, ws)
            record_direct_assignment(user_id, project_id, role_id, actor_id, may_add_member=may_add)
        row = role_service.assign_role(user_id, project_id, role_id, granted_by=actor_id)
        if workspace_id:
            sync(workspace_id, [user_id], actor=actor_id)
            row = role_service.get_user_role(user_id, project_id) or row
    return row


def revoke_direct_project_role(user_id: str, project_id: str, actor: Optional[dict] = None) -> None:
    """Counterpart of assign_direct_project_role: the role row and the workspace exception that
    keeps it revoked commit together."""
    with _store().transaction():
        record_direct_assignment(user_id, project_id, None, (actor or {}).get("user_id"))
        role_service.revoke_role(user_id, project_id)

def on_project_created(user: dict, project_id: str, workspace_id: str) -> None:
    """Place a new project in its workspace and give the creator Project Admin access when the
    role default would not (a creator must be able to open what they made)."""
    ws = _require(workspace_id)
    _db.set_project_workspace(project_id, workspace_id)
    uid = user.get("user_id")
    role = member_role(workspace_id, uid)
    if role and not _is_sys_admin(user) and _default_access(ws, role) != "admin":
        _db.set_exception(workspace_id, project_id, uid, "admin", uid)
    sync(workspace_id, project_ids=[project_id], actor=uid)


def resolve_create_workspace(user: dict, workspace_id: Optional[str]) -> str:
    """The workspace a new project goes to: the requested one, else the default. Raises when the
    caller may not create a project there (공용 정책 › 새 프로젝트를 만들 수 있는 사람)."""
    target = workspace_id or ensure_default_workspace()["workspace_id"]
    ws = _db.get(target)
    if not ws:
        raise WorkspaceError(404, "workspace_not_found")
    if ws["status"] != "active":
        raise WorkspaceError(409, "workspace_disabled")
    if not can_create_project(user, target):
        raise WorkspaceError(403, "project_create_forbidden")
    return target


# ── policy ───────────────────────────────────────────────────────────────────

def set_policy(user: dict, workspace_id: str, ai_mode: Optional[str] = None,
               project_create: Optional[str] = None) -> dict:
    ws = _require(workspace_id)
    _check(can_manage(user, ws))
    updates = {}
    if ai_mode is not None:
        if ai_mode not in AI_MODES:
            raise WorkspaceError(422, "ai_mode_invalid")
        updates["ai_policy_mode"] = AI_MODES[ai_mode]
    if project_create is not None:
        if project_create not in PROJECT_CREATE_POLICIES:
            raise WorkspaceError(422, "project_create_invalid")
        updates["project_create_policy"] = project_create
    _db.update(workspace_id, updates)
    return get_detail(user, workspace_id)


def _resource_names() -> dict[tuple[str, str], dict]:
    names: dict[tuple[str, str], dict] = {}
    for row in _store()._fetch_all(
        "SELECT provider_id, name, kind, enabled FROM ai_providers "
        "WHERE project_id IS NULL AND COALESCE(owner_scope, 'system') = 'system' ORDER BY sort_order, name"
    ):
        names[("provider", row["provider_id"])] = {"name": row["name"], "kind": row.get("kind"),
                                                  "enabled": bool(row.get("enabled"))}
    try:
        agents = _store()._fetch_all("SELECT agent_id, name, enabled FROM agents ORDER BY name")
    except Exception:  # noqa: BLE001 — an install without the agent registry lists no agents
        agents = []
    for row in agents:
        names[("agent", row["agent_id"])] = {"name": row["name"], "enabled": bool(row.get("enabled"))}
    return names


def list_policy_resources(user: dict, workspace_id: str) -> dict:
    ws = _require(workspace_id)
    _check(can_manage(user, ws))
    providers, agents = [], []
    for (kind, rid), info in _resource_names().items():
        item = {"resource_type": kind, "resource_id": rid, "name": info["name"], "enabled": info["enabled"]}
        (providers if kind == "provider" else agents).append(item)
    return {"providers": providers, "agents": agents}


def allow_resource(user: dict, workspace_id: str, resource_type: str, resource_id: str) -> dict:
    ws = _require(workspace_id)
    _check(can_manage(user, ws))
    if resource_type not in RESOURCE_TYPES:
        raise WorkspaceError(422, "resource_type_invalid")
    if (resource_type, resource_id) not in _resource_names():
        raise WorkspaceError(404, "resource_not_found")
    _db.upsert_allow_entry(workspace_id, resource_type, resource_id, "approved", "admin", user.get("user_id"))
    return get_detail(user, workspace_id)


def revoke_resource(user: dict, workspace_id: str, resource_type: str, resource_id: str) -> dict:
    """Removing a chip revokes the approval (the row stays as history): a new run is refused at
    once; a run already started is not stopped (R0001 §4.6)."""
    ws = _require(workspace_id)
    _check(can_manage(user, ws))
    if not _db.get_allow_entry(workspace_id, resource_type, resource_id):
        raise WorkspaceError(404, "resource_not_found")
    _db.upsert_allow_entry(workspace_id, resource_type, resource_id, "revoked", "admin", user.get("user_id"))
    return get_detail(user, workspace_id)


def allowed_resource_ids(project_id: str, resource_type: str = "provider") -> Optional[set[str]]:
    """None when the project's workspace does not limit AI (policy 'off'); otherwise the approved
    ids. The server enforces this where it starts a run (ai_settings_service.resolve_effective)."""
    workspace_id = workspace_id_of_project(project_id)
    if not workspace_id:
        return None
    ws = _db.get(workspace_id)
    if not ws or ws.get("ai_policy_mode") != "allowlist":
        return None
    return {
        e["resource_id"] for e in _db.list_allowlist(workspace_id)
        if e["resource_type"] == resource_type and e["status"] == "approved"
    }


def is_agent_allowed(project_id: str, agent_id: str) -> bool:
    allowed = allowed_resource_ids(project_id, "agent")
    return allowed is None or agent_id in allowed
