"""RBAC permission-checking dependencies.

Every check goes through the one judgment ``rbac.access.can`` (design 0004-D §2.2):
system administrators pass, ``system.*`` is for them only, and a ``project.*`` permission
needs the caller's one role on that project to hold it. A role on ``__SYSTEM__`` is never
added to a project.
"""
from __future__ import annotations

from fastapi import Depends, HTTPException, Request

from modules.flow_gate.auth.middleware import get_current_user
from modules.flow_gate.rbac import permission_service as _permission_service


def _has_permission(user: dict, permission: str, project_id: str | None) -> bool:
    from modules.flow_gate.rbac import access

    return access.can(user, project_id, permission)


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
    """The caller's effective role on the path's project must be ``role_id`` (aliases read
    as their fixed role). System administrators pass."""

    wanted = _permission_service.fixed_role(role_id) or role_id

    def _checker(
        request: Request,
        user: dict = Depends(get_current_user),
    ):
        if user.get("is_admin"):
            return user

        project_id = request.path_params.get(project_id_param)
        if not project_id:
            raise HTTPException(status_code=403, detail="Forbidden")
        if _permission_service.effective_role(user.get("user_id"), project_id) != wanted:
            raise HTTPException(status_code=403, detail="Forbidden")
        return user

    return _checker
