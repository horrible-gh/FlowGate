"""Role service — assign, revoke, and query user roles.

Supported roles: role_admin / role_manager / role_worker / role_viewer
System roles are assigned with project_id='__SYSTEM__' (D011 r1 §4-1 method A).
"""
from __future__ import annotations

from typing import Optional

from modules.flow_gate.db.connection import get_store, now_iso
from modules.flow_gate.rbac.permission_service import invalidate_cache
from modules.flow_gate.db import roles as db_roles

SYSTEM_PROJECT = "__SYSTEM__"


def get_user_role(user_id: str, project_id: str) -> Optional[dict]:
    """Return the user_project_roles row for the given user_id and project_id."""
    return get_store()._fetch_one(
        "SELECT * FROM user_project_roles WHERE user_id = ? AND project_id = ?",
        [user_id, project_id],
    )


def list_user_roles(user_id: str) -> list[dict]:
    """List all project roles for the user (includes project and role names)."""
    return get_store()._fetch_all(
        """
        SELECT upr.*, r.role_name, p.project_name
        FROM user_project_roles upr
        JOIN roles r ON upr.role_id = r.role_id
        LEFT JOIN projects p ON upr.project_id = p.project_id
        WHERE upr.user_id = ?
        ORDER BY upr.project_id
        """,
        [user_id],
    )


def list_project_members(project_id: str) -> list[dict]:
    """All project members with their role information."""
    return get_store()._fetch_all(
        """
        SELECT upr.user_id, upr.project_id, upr.role_id,
               upr.granted_at, upr.granted_by,
               u.username, u.email,
               r.role_name
        FROM user_project_roles upr
        JOIN users u ON upr.user_id = u.user_id
        JOIN roles r ON upr.role_id = r.role_id
        WHERE upr.project_id = ?
        ORDER BY r.role_id, u.username
        """,
        [project_id],
    )


def assign_role(
    user_id: str,
    project_id: str,
    role_id: str,
    granted_by: Optional[str] = None,
) -> dict:
    """Assign a role to a user (replace if already exists).

    Invalidates permission cache after role change.
    role_id must exist in the DB.
    """
    existing_role = db_roles.get_by_id(role_id)
    if not existing_role:
        raise ValueError(f"Invalid role: {role_id}")

    store = get_store()
    now = now_iso()
    existing = get_user_role(user_id, project_id)

    if existing:
        store._execute(
            "UPDATE user_project_roles SET role_id = ?, granted_at = ?, granted_by = ? "
            "WHERE user_id = ? AND project_id = ?",
            [role_id, now, granted_by, user_id, project_id],
        )
    else:
        store._execute(
            "INSERT INTO user_project_roles (user_id, project_id, role_id, granted_at, granted_by) "
            "VALUES (?, ?, ?, ?, ?)",
            [user_id, project_id, role_id, now, granted_by],
        )

    invalidate_cache(user_id, project_id)
    return get_user_role(user_id, project_id)  # type: ignore[return-value]


def revoke_role(user_id: str, project_id: str) -> None:
    """Revoke a user's project role. Invalidates the permission cache."""
    get_store()._execute(
        "DELETE FROM user_project_roles WHERE user_id = ? AND project_id = ?",
        [user_id, project_id],
    )
    invalidate_cache(user_id, project_id)
