"""Common authentication helper for outbound query APIs (D021 §7-2).

Bearer token → worker token or user access JWT verification
"""
from __future__ import annotations

from typing import Optional

import jwt
from fastapi import Request
from fastapi.responses import JSONResponse

from modules.flow_gate.rbac.permission_service import has_permission
from modules.flow_gate.services import token_service
from modules.flow_gate.utils.help_url import help_url


def _fail(status: int, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={
            "ok": False,
            "http_status": status,
            "error_message": message,
            "help_url": help_url(),
        },
    )


def _extract_bearer(request: Request) -> Optional[str]:
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[len("Bearer "):]
    return None


def _load_active_user(user_id: str):
    """The user row through the shared auth cache (invalidated on user updates)."""
    from modules.flow_gate.auth import auth_cache
    from modules.flow_gate.db import users as db_users

    return auth_cache.user_cache().get_or_load(user_id, lambda: db_users.get_by_id(user_id))


def _verify_user_jwt(raw: str):
    from modules.flow_gate.auth.jwt_service import decode_token
    from modules.flow_gate.auth.token_store import is_blacklisted

    try:
        payload = decode_token(raw)
    except (jwt.ExpiredSignatureError, jwt.InvalidTokenError):
        return None

    if payload.get("type") != "access":
        return None
    if payload.get("totp_pending") is True:
        return None

    jti = payload.get("jti")
    if jti:
        try:
            if is_blacklisted(jti):
                return _fail(401, "Token has been revoked")
        except Exception as exc:
            from modules.flow_gate.db.resilience import auth_unavailable
            error = auth_unavailable(exc)
            return _fail(error.status_code, error.detail)

    subject = payload.get("sub")
    if not subject:
        return None

    # Same account/session rules as the console session path (auth.middleware):
    # a revoked session or a disabled account stops the token at once.
    try:
        from modules.flow_gate.auth.session_store import is_session_active

        sid = payload.get("sid")
        if sid and not is_session_active(sid):
            return _fail(401, "session_revoked")
        user = _load_active_user(subject)
    except Exception as exc:
        from modules.flow_gate.db.resilience import auth_unavailable
        error = auth_unavailable(exc)
        return _fail(error.status_code, error.detail)
    if user is None:
        return _fail(401, "User not found")
    if not user.get("is_active"):
        return _fail(401, "User account is inactive")

    return {
        **payload,
        "issued_to": subject,
        "_is_user_jwt": True,
    }


def verify_bearer(request: Request):
    """Verify Bearer token.

    - Worker token: token_service.verify + project.document.read check
    - User JWT: pass after access/totp/blacklist verification

    Success: returns token_rec dict
    Failure: returns JSONResponse (caller must return immediately)
    """
    raw = _extract_bearer(request)
    if raw is None:
        return _fail(401, "Authorization header is required")

    from fastapi import HTTPException
    try:
        token_rec = token_service.verify(raw)
    except HTTPException as exc:
        user_token = _verify_user_jwt(raw)
        if user_token is not None:
            return user_token
        return _fail(exc.status_code, exc.detail)
    except Exception as exc:
        from modules.flow_gate.db.resilience import unavailable, auth_unavailable
        if not unavailable(exc):
            raise
        error = auth_unavailable(exc)
        return _fail(error.status_code, error.detail)

    user_id: str = token_rec["issued_to"]
    project: str = token_rec["project"]

    if not has_permission(user_id, project, "project.document.read"):
        return _fail(403, "You do not have permission to perform this action")

    return token_rec


_READ = "project.document.read"


def _jwt_user(auth: dict) -> Optional[dict]:
    """The caller of a user JWT in the shape ``rbac.access.can`` judges.

    None for a worker token. The administrator flag comes from the stored user
    row (shared auth cache), never from the token payload.
    """
    if not auth.get("_is_user_jwt"):
        return None
    user_id = auth.get("issued_to")
    row = _load_active_user(user_id) if user_id else None
    return {"user_id": user_id, "is_admin": bool((row or {}).get("is_admin"))}


def can_read_project(auth: dict, project_id: str) -> bool:
    """May the caller read ``project_id``.

    A user JWT goes through the one judgment ``rbac.access.can``: administrators
    pass, anyone else must be a member of that project (a role on ``__SYSTEM__``
    alone is not membership) and hold ``project.document.read``. A worker token keeps
    its existing rule, the owner's permission on the target project.
    """
    user_id = auth.get("issued_to")
    if not (user_id and project_id):
        return False
    user = _jwt_user(auth)
    if user is not None:
        from modules.flow_gate.rbac import access

        return access.can(user, project_id, _READ)
    return bool(has_permission(user_id, project_id, _READ))


def readable_project_clause(auth: dict, column: str) -> tuple[str, list]:
    """SQL fragment (starting with ``AND``) limiting ``column`` to readable projects.

    The same rule as ``can_read_project`` evaluated by the database, so lists never
    load every project to filter it in Python. ``column`` must be qualified with
    the outer table alias (for example ``g.project_id``); a bare name could bind to
    another joined table and detach the permission from the listed row's project,
    so it is refused.
    """
    from modules.flow_gate.rbac import access

    column = access.outer_column(column)
    user_id = auth.get("issued_to")
    if not user_id:
        return " AND 1 = 0", []
    user = _jwt_user(auth)
    if user is not None:
        return access.member_permission_clause(user, _READ, column)
    # Worker token: the owner's current permission on the row's project, the same
    # rule as ``has_permission(user_id, project_id, project.document.read)``.
    return access.permitted_project_clause(user_id, _READ, column)


def readable_projects(auth: dict) -> list[str]:
    """IDs of the projects the caller may read, selected in SQL."""
    from modules.flow_gate.db.connection import get_store

    sql, params = readable_project_clause(auth, "p.project_id")
    rows = get_store()._fetch_all(
        "SELECT p.project_id AS project_id FROM projects p WHERE 1 = 1" + sql
        + " ORDER BY p.project_id",
        params,
    )
    return [r["project_id"] for r in rows]
