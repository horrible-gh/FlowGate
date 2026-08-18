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
from modules.flow_gate.rbac import permission_service as _permission_service

_SYSTEM_PROJECT = "__SYSTEM__"



def _has_permission(user: dict, permission: str, project_id: str | None) -> bool:
    if user.get("is_admin"):
        return True
    user_id = user.get("user_id")
    if not user_id:
        return False

    # 0276 NR0003 finding 2: these were the remaining two of the five fixed
    # per-request auth queries, on every permission-protected route.
    #
    # rbac/permission_service.py already solves exactly this: it resolves the
    # user's granted permission set in a *single* JOIN, caches it, and is already
    # invalidated by role_service.assign_role()/revoke_role(). This function
    # simply never used it and issued its own two uncached queries instead.
    # Delegating removes the duplication rather than adding a second cache.
    #
    # Set equivalence: permission_service filters
    # `upr.project_id IN (?, '__SYSTEM__')`, which for project_id=__SYSTEM__
    # (the no-project case here) collapses to the system roles alone — the same
    # set the two-step query produced.
    return _permission_service.has_permission(
        user_id, project_id or _SYSTEM_PROJECT, permission
    )



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
