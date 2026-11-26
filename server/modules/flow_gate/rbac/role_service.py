"""Role service — direct and team project roles.

Project roles are the four fixed roles (``permission_service.FIXED_ROLES``). A role id given
as an old alias (``role_manager`` / ``role_worker``) is stored as its fixed role. Anything
else is refused with ``ValueError``.

``user_project_roles`` holds direct grants only (workspace values are computed at judgment
time, design 0004-D §2.2-C). A row on ``__SYSTEM__`` is still stored as given (it is shown as
a system role in the user settings) but never used by a judgment.

Every change runs in one transaction together with the permission version of everyone it
affects (``permission_service.bump_user_versions``), so either both commit or neither does,
and the next judgment in any process sees the change (design 3.3).
"""
from __future__ import annotations

import uuid
from typing import Optional

from modules.flow_gate.db.connection import get_store, now_iso
from modules.flow_gate.rbac import permission_service as _permission_service
from modules.flow_gate.db import roles as db_roles

SYSTEM_PROJECT = "__SYSTEM__"


def project_role_id(role_id: Optional[str]) -> str:
    """The fixed role a project grant is stored as, or ValueError."""
    role = _permission_service.fixed_role(role_id)
    if role is None:
        raise ValueError(f"Invalid role: {role_id}")
    return role


def get_user_role(user_id: str, project_id: str) -> Optional[dict]:
    """Return the user_project_roles row for the given user_id and project_id."""
    return get_store()._fetch_one(
        "SELECT * FROM user_project_roles WHERE user_id = ? AND project_id = ?",
        [user_id, project_id],
    )


def list_user_roles(user_id: str) -> list[dict]:
    """List all direct project roles for the user (includes project and role names)."""
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
    """Direct project members with their role information."""
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
    """Give a user a direct role on a project (replace if one exists).

    A project role must be one of the four fixed roles (an alias is stored as its fixed role).
    A ``__SYSTEM__`` row keeps the given id, which must exist in ``roles``.
    """
    if project_id == SYSTEM_PROJECT:
        if not db_roles.get_by_id(role_id):
            raise ValueError(f"Invalid role: {role_id}")
        stored = role_id
    else:
        stored = project_role_id(role_id)

    store = get_store()
    now = now_iso()
    with store.transaction():
        existing = get_user_role(user_id, project_id)
        if existing:
            store._execute(
                "UPDATE user_project_roles SET role_id = ?, granted_at = ?, granted_by = ? "
                "WHERE user_id = ? AND project_id = ?",
                [stored, now, granted_by, user_id, project_id],
            )
        else:
            store._execute(
                "INSERT INTO user_project_roles (user_id, project_id, role_id, granted_at, granted_by) "
                "VALUES (?, ?, ?, ?, ?)",
                [user_id, project_id, stored, now, granted_by],
            )
        _permission_service.bump_user_versions([user_id], store)
    return get_user_role(user_id, project_id)  # type: ignore[return-value]


def revoke_role(user_id: str, project_id: str) -> None:
    """Remove a user's direct role on a project."""
    store = get_store()
    with store.transaction():
        store._execute(
            "DELETE FROM user_project_roles WHERE user_id = ? AND project_id = ?",
            [user_id, project_id],
        )
        _permission_service.bump_user_versions([user_id], store)


# ── teams (query 643: teams exist; the API and screens are cards 4·5) ─────────

def get_team(team_id: str) -> Optional[dict]:
    return get_store()._fetch_one("SELECT * FROM teams WHERE team_id = ?", [team_id])


def team_member_ids(team_id: str) -> list[str]:
    rows = get_store()._fetch_all("SELECT user_id FROM team_members WHERE team_id = ?", [team_id])
    return [row["user_id"] for row in rows]


def create_team(workspace_id: str, name: str, created_by: Optional[str] = None) -> dict:
    """A team inside one workspace (no grant yet, so no version changes)."""
    name = (name or "").strip()
    if not name:
        raise ValueError("Team name is required")
    team_id = f"team_{uuid.uuid4().hex[:12]}"
    now = now_iso()
    get_store()._execute(
        "INSERT INTO teams (team_id, workspace_id, name, created_by, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        [team_id, workspace_id, name, created_by, now, now],
    )
    return get_team(team_id)  # type: ignore[return-value]


def delete_team(team_id: str) -> None:
    """Delete a team, its memberships and its project roles; its members' versions rise."""
    store = get_store()
    with store.transaction():
        members = team_member_ids(team_id)
        store._execute("DELETE FROM team_project_roles WHERE team_id = ?", [team_id])
        store._execute("DELETE FROM team_members WHERE team_id = ?", [team_id])
        store._execute("DELETE FROM teams WHERE team_id = ?", [team_id])
        _permission_service.bump_user_versions(members, store)


def add_team_member(team_id: str, user_id: str, added_by: Optional[str] = None) -> None:
    store = get_store()
    with store.transaction():
        exists = store._fetch_one(
            "SELECT 1 AS ok FROM team_members WHERE team_id = ? AND user_id = ?", [team_id, user_id]
        )
        if not exists:
            store._execute(
                "INSERT INTO team_members (team_id, user_id, added_by, created_at) VALUES (?, ?, ?, ?)",
                [team_id, user_id, added_by, now_iso()],
            )
        _permission_service.bump_user_versions([user_id], store)


def remove_team_member(team_id: str, user_id: str) -> None:
    store = get_store()
    with store.transaction():
        store._execute("DELETE FROM team_members WHERE team_id = ? AND user_id = ?", [team_id, user_id])
        _permission_service.bump_user_versions([user_id], store)


def get_team_role(team_id: str, project_id: str) -> Optional[dict]:
    return get_store()._fetch_one(
        "SELECT * FROM team_project_roles WHERE team_id = ? AND project_id = ?", [team_id, project_id]
    )


def assign_team_role(team_id: str, project_id: str, role_id: str, granted_by: Optional[str] = None) -> dict:
    """Give a team a role on a project (replace if one exists); every member's version rises."""
    stored = project_role_id(role_id)
    store = get_store()
    now = now_iso()
    with store.transaction():
        if get_team_role(team_id, project_id):
            store._execute(
                "UPDATE team_project_roles SET role_id = ?, granted_by = ?, granted_at = ?"
                " WHERE team_id = ? AND project_id = ?",
                [stored, granted_by, now, team_id, project_id],
            )
        else:
            store._execute(
                "INSERT INTO team_project_roles (team_id, project_id, role_id, granted_by, granted_at)"
                " VALUES (?, ?, ?, ?, ?)",
                [team_id, project_id, stored, granted_by, now],
            )
        _permission_service.bump_user_versions(team_member_ids(team_id), store)
    return get_team_role(team_id, project_id)  # type: ignore[return-value]


def revoke_team_role(team_id: str, project_id: str) -> None:
    store = get_store()
    with store.transaction():
        store._execute(
            "DELETE FROM team_project_roles WHERE team_id = ? AND project_id = ?", [team_id, project_id]
        )
        _permission_service.bump_user_versions(team_member_ids(team_id), store)
