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

_HELP_URL = "https://example.com/api/v1/help"


def _fail(status: int, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={
            "ok": False,
            "http_status": status,
            "error_message": message,
            "help_url": _HELP_URL,
        },
    )


def _extract_bearer(request: Request) -> Optional[str]:
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[len("Bearer "):]
    return None


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
        except Exception:
            return None

    subject = payload.get("sub")
    if not subject:
        return None

    return {
        **payload,
        "issued_to": subject,
        "_is_user_jwt": True,
    }


def verify_bearer(request: Request):
    """Verify Bearer token.

    - Worker token: token_service.verify + perm_document_read check
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

    user_id: str = token_rec["issued_to"]
    project: str = token_rec["project"]

    if not has_permission(user_id, project, "perm_document_read"):
        return _fail(403, "You do not have permission to perform this action")

    return token_rec
