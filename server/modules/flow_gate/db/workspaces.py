"""Workspace CRUD (flowgate.v02.0020, migration 136).

Every function reads the store through the `connection` module attribute, so a test that swaps
`connection.get_store` reaches these queries as well.
"""
from __future__ import annotations

from typing import Any, Optional

from . import connection as _connection
from .connection import now_iso

DEFAULT_WORKSPACE_ID = "ws_default"
SYSTEM_PROJECT = "__SYSTEM__"


def _store():
    return _connection.get_store()


# ── workspaces ───────────────────────────────────────────────────────────────

def get(workspace_id: str) -> Optional[dict]:
    return _store()._fetch_one("SELECT * FROM workspaces WHERE workspace_id = ?", [workspace_id])


def get_by_name_key(name_key: str) -> Optional[dict]:
    return _store()._fetch_one("SELECT * FROM workspaces WHERE name_key = ?", [name_key])


def get_default() -> Optional[dict]:
    return _store()._fetch_one(
        "SELECT * FROM workspaces WHERE is_default = 1 ORDER BY created_at, workspace_id LIMIT 1"
    )


def list_all() -> list[dict]:
    return _store()._fetch_all("SELECT * FROM workspaces ORDER BY is_default DESC, created_at, workspace_id")


def create(data: dict[str, Any]) -> dict:
    now = now_iso()
    _store()._execute(
        "INSERT INTO workspaces (workspace_id, name, name_key, status, is_default, ai_policy_mode, "
        "project_create_policy, default_access_owner, default_access_admin, default_access_member, "
        "created_by, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            data["workspace_id"], data["name"], data["name_key"], data.get("status", "active"),
            int(data.get("is_default", 0)), data.get("ai_policy_mode", "off"),
            data.get("project_create_policy", "owner_admin"),
            data.get("default_access_owner", "admin"), data.get("default_access_admin", "dev"),
            data.get("default_access_member", "none"), data.get("created_by"), now, now,
        ],
    )
    return get(data["workspace_id"])  # type: ignore[return-value]


def update(workspace_id: str, updates: dict[str, Any]) -> Optional[dict]:
    updates = {k: v for k, v in updates.items() if k not in ("workspace_id", "created_at", "created_by")}
    if updates:
        updates["updated_at"] = now_iso()
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        _store()._execute(
            f"UPDATE workspaces SET {set_clause} WHERE workspace_id = ?",
            [*updates.values(), workspace_id],
        )
    return get(workspace_id)


def delete(workspace_id: str) -> None:
    _store()._execute("DELETE FROM workspaces WHERE workspace_id = ?", [workspace_id])


# ── projects ─────────────────────────────────────────────────────────────────

def list_projects(workspace_id: str, include_unassigned: bool = False) -> list[dict]:
    """Projects in a workspace. The default workspace also owns rows whose workspace_id is NULL
    (a project inserted by a path that predates migration 136)."""
    sql = (
        "SELECT project_id, project_name, is_active, color, workspace_id FROM projects "
        "WHERE project_id <> ? AND (workspace_id = ?"
        + (" OR workspace_id IS NULL" if include_unassigned else "")
        + ") ORDER BY project_name, project_id"
    )
    return _store()._fetch_all(sql, [SYSTEM_PROJECT, workspace_id])


def project_counts() -> dict[Optional[str], int]:
    rows = _store()._fetch_all(
        "SELECT workspace_id, COUNT(*) AS cnt FROM projects WHERE project_id <> ? GROUP BY workspace_id",
        [SYSTEM_PROJECT],
    )
    return {row["workspace_id"]: int(row["cnt"]) for row in rows}


def set_project_workspace(project_id: str, workspace_id: str) -> None:
    from . import meta_cache

    _store()._execute(
        "UPDATE projects SET workspace_id = ?, updated_at = ? WHERE project_id = ?",
        [workspace_id, now_iso(), project_id],
    )
    meta_cache.invalidate_project(project_id)


# ── members ──────────────────────────────────────────────────────────────────

def list_members(workspace_id: str) -> list[dict]:
    return _store()._fetch_all(
        "SELECT m.workspace_id, m.user_id, m.role, m.created_at AS joined_at, "
        "u.username, u.email, u.is_active, u.is_admin, u.last_login_at, u.invited_at "
        "FROM workspace_members m JOIN users u ON u.user_id = m.user_id "
        "WHERE m.workspace_id = ? ORDER BY "
        "CASE m.role WHEN 'owner' THEN 0 WHEN 'admin' THEN 1 ELSE 2 END, u.username",
        [workspace_id],
    )


def get_member(workspace_id: str, user_id: str) -> Optional[dict]:
    return _store()._fetch_one(
        "SELECT * FROM workspace_members WHERE workspace_id = ? AND user_id = ?",
        [workspace_id, user_id],
    )


def member_counts() -> dict[str, int]:
    rows = _store()._fetch_all(
        "SELECT workspace_id, COUNT(*) AS cnt FROM workspace_members GROUP BY workspace_id"
    )
    return {row["workspace_id"]: int(row["cnt"]) for row in rows}


def owners() -> dict[str, dict]:
    rows = _store()._fetch_all(
        "SELECT m.workspace_id, m.user_id, u.username, u.email FROM workspace_members m "
        "JOIN users u ON u.user_id = m.user_id WHERE m.role = 'owner'"
    )
    return {row["workspace_id"]: row for row in rows}


def memberships_for_user(user_id: str) -> list[dict]:
    return _store()._fetch_all(
        "SELECT m.workspace_id, m.role, w.name, w.status FROM workspace_members m "
        "JOIN workspaces w ON w.workspace_id = m.workspace_id WHERE m.user_id = ? ORDER BY w.name",
        [user_id],
    )


def all_memberships() -> list[dict]:
    return _store()._fetch_all(
        "SELECT m.user_id, m.workspace_id, m.role, w.name FROM workspace_members m "
        "JOIN workspaces w ON w.workspace_id = m.workspace_id ORDER BY w.is_default DESC, w.name"
    )


def add_member(workspace_id: str, user_id: str, role: str, added_by: Optional[str]) -> None:
    now = now_iso()
    _store()._execute(
        "INSERT INTO workspace_members (workspace_id, user_id, role, added_by, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [workspace_id, user_id, role, added_by, now, now],
    )


def set_member_role(workspace_id: str, user_id: str, role: str) -> None:
    _store()._execute(
        "UPDATE workspace_members SET role = ?, updated_at = ? WHERE workspace_id = ? AND user_id = ?",
        [role, now_iso(), workspace_id, user_id],
    )


def remove_member(workspace_id: str, user_id: str) -> None:
    _store()._execute(
        "DELETE FROM workspace_members WHERE workspace_id = ? AND user_id = ?", [workspace_id, user_id]
    )


# ── per-person project access exceptions ─────────────────────────────────────

def list_exceptions(workspace_id: str) -> list[dict]:
    return _store()._fetch_all(
        "SELECT project_id, user_id, access FROM workspace_project_access WHERE workspace_id = ?",
        [workspace_id],
    )


def get_exception(project_id: str, user_id: str) -> Optional[dict]:
    return _store()._fetch_one(
        "SELECT * FROM workspace_project_access WHERE project_id = ? AND user_id = ?",
        [project_id, user_id],
    )


def set_exception(workspace_id: str, project_id: str, user_id: str, access: str,
                  updated_by: Optional[str]) -> None:
    now = now_iso()
    if get_exception(project_id, user_id):
        _store()._execute(
            "UPDATE workspace_project_access SET access = ?, workspace_id = ?, updated_by = ?, updated_at = ? "
            "WHERE project_id = ? AND user_id = ?",
            [access, workspace_id, updated_by, now, project_id, user_id],
        )
    else:
        _store()._execute(
            "INSERT INTO workspace_project_access (project_id, user_id, workspace_id, access, updated_by, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [project_id, user_id, workspace_id, access, updated_by, now],
        )


def clear_exception(project_id: str, user_id: str) -> None:
    _store()._execute(
        "DELETE FROM workspace_project_access WHERE project_id = ? AND user_id = ?", [project_id, user_id]
    )


def clear_member_exceptions(workspace_id: str, user_id: str) -> None:
    _store()._execute(
        "DELETE FROM workspace_project_access WHERE workspace_id = ? AND user_id = ?", [workspace_id, user_id]
    )


def clear_project_exceptions(project_id: str) -> None:
    _store()._execute("DELETE FROM workspace_project_access WHERE project_id = ?", [project_id])


# ── AI allowlist ─────────────────────────────────────────────────────────────

def list_allowlist(workspace_id: str) -> list[dict]:
    return _store()._fetch_all(
        "SELECT * FROM workspace_ai_allowlist WHERE workspace_id = ? ORDER BY resource_type, created_at, resource_id",
        [workspace_id],
    )


def get_allow_entry(workspace_id: str, resource_type: str, resource_id: str) -> Optional[dict]:
    return _store()._fetch_one(
        "SELECT * FROM workspace_ai_allowlist WHERE workspace_id = ? AND resource_type = ? AND resource_id = ?",
        [workspace_id, resource_type, resource_id],
    )


def upsert_allow_entry(workspace_id: str, resource_type: str, resource_id: str, status: str,
                       source: str, actor: Optional[str]) -> None:
    now = now_iso()
    if get_allow_entry(workspace_id, resource_type, resource_id):
        _store()._execute(
            "UPDATE workspace_ai_allowlist SET status = ?, decided_by = ?, updated_at = ? "
            "WHERE workspace_id = ? AND resource_type = ? AND resource_id = ?",
            [status, actor, now, workspace_id, resource_type, resource_id],
        )
    else:
        _store()._execute(
            "INSERT INTO workspace_ai_allowlist (workspace_id, resource_type, resource_id, status, source, "
            "requested_by, decided_by, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [workspace_id, resource_type, resource_id, status, source, actor, actor, now, now],
        )
