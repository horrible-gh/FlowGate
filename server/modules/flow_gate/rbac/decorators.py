"""RBAC permission-checking dependencies.

D011 r1 §5.2 consistency:
  - Query roles from user_project_roles using user_id + project_id IN (target_project_id, '__SYSTEM__')
  - Verify permissions in role_permissions
  - Users with is_admin=1 bypass all permission checks
"""
from __future__ import annotations

from fastapi import Depends, HTTPException, Request

from modules.flow_gate.auth.middleware import get_current_user
from modules.flow_gate.db.connection import get_store

_SYSTEM_PROJECT = "__SYSTEM__"



def _has_permission(user: dict, permission: str, project_id: str | None) -> bool:
    if user.get("is_admin"):
        return True
    user_id = user.get("user_id")
    if not user_id:
        return False

    store = get_store()
    project_ids = [_SYSTEM_PROJECT]
    if project_id and project_id != _SYSTEM_PROJECT:
        project_ids.append(project_id)

    placeholders = ", ".join("?" for _ in project_ids)
    roles = store._fetch_all(
        f"SELECT role_id FROM user_project_roles WHERE user_id = ? AND project_id IN ({placeholders})",
        [user_id, *project_ids],
    )
    if not roles:
        return False

    role_ids = [row["role_id"] for row in roles]
    perm_placeholders = ", ".join("?" for _ in role_ids)
    perms = store._fetch_all(
        f"SELECT permission_id FROM role_permissions WHERE role_id IN ({perm_placeholders})",
        role_ids,
    )
    return permission in {row["permission_id"] for row in perms}



def require_permission(permission: str, project_id_param: str | None = None):
    """Permission check dependency factory."""

    def _checker(
        request: Request,
        user: dict = Depends(get_current_user),
    ):
        project_id: str | None = None
        if project_id_param:
            project_id = request.path_params.get(project_id_param)
        if not _has_permission(user, permission, project_id):
            raise HTTPException(status_code=403, detail="Forbidden")
        return user

    return _checker



def require_system_permission(permission: str):
    return require_permission(permission)



def require_role(role_id: str, project_id_param: str = "project_id"):
    def _checker(
        request: Request,
        user: dict = Depends(get_current_user),
    ):
        if user.get("is_admin"):
            return user

        project_id = request.path_params.get(project_id_param)
        if not project_id:
            raise HTTPException(status_code=403, detail="Forbidden")

        store = get_store()
        rows = store._fetch_all(
            "SELECT role_id FROM user_project_roles WHERE user_id = ? AND project_id IN (?, ?)",
            [user.get("user_id"), project_id, _SYSTEM_PROJECT],
        )
        if role_id not in {row["role_id"] for row in rows}:
            raise HTTPException(status_code=403, detail="Forbidden")
        return user

    return _checker
