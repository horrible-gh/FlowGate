"""Request authentication middleware and the current_user FastAPI dependency.

verify_token  → Check the jti blacklist + validate JWT signature and expiration
get_current_user → After verify_token, return the user row from the DB
"""
from __future__ import annotations

import os

from fastapi import Depends, HTTPException
from fastapi.security import OAuth2PasswordBearer

import jwt

from .jwt_service import decode_token
from .token_store import is_blacklisted

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")


def _worker_token_detail(token: str) -> str | None:
    """Return a corrective 401 message when `token` is a valid WORKER token.

    The internal UI API and the worker-facing outbound API sit one character
    apart ({ctx}/api/v1/documents/... vs .../document/...), and only the former
    reaches this dependency. A worker token is not a JWT, so presenting it to a
    UI route fails signature decoding and yields a bare "Invalid authentication
    credentials" — indistinguishable from an expired or revoked token. In group
    0238 a worker read that 401 as proof its token lacked a document-read scope
    and reported a nonexistent bug (NR0003). Name the real cause instead.

    Returns None for anything that is not a live worker token, so a genuinely
    bad credential still gets the generic message and leaks nothing.
    """
    try:
        from modules.flow_gate.services import token_service
        token_service.verify(token)  # read-only; consumption is a separate call
    except Exception:
        return None

    from modules.flow_gate.utils.help_url import help_url, outbound_api_base
    base = outbound_api_base()
    return (
        "This is a FlowGate worker token, which the internal UI API does not accept — "
        "it requires a signed-in user session. Worker tokens authenticate the outbound "
        f"API instead: use GET {base}/document/{{doc_id}} (singular 'document') to read a "
        f"document body. See {help_url()} for the endpoints a worker token can call."
    )


def verify_token(token: str = Depends(oauth2_scheme)) -> dict:
    """Validate the JWT and return its payload.

    - Check the jti blacklist
    - Verify signature and expiration
    - Reject if type != 'access'
    - Reject if totp_pending is True
    """
    credentials_exc = HTTPException(
        status_code=401,
        detail="Invalid authentication credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    try:
        payload = decode_token(token)
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=401,
            detail="Token has expired",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except jwt.InvalidTokenError:
        # Not a JWT at all — most often a worker token aimed at the wrong API.
        detail = _worker_token_detail(token)
        if detail is not None:
            raise HTTPException(
                status_code=401,
                detail=detail,
                headers={"WWW-Authenticate": "Bearer"},
            )
        raise credentials_exc

    if payload.get("type") != "access":
        raise credentials_exc

    if payload.get("totp_pending"):
        raise HTTPException(status_code=401, detail="2FA verification required")

    jti = payload.get("jti")
    if jti:
        try:
            if is_blacklisted(jti):
                raise HTTPException(status_code=401, detail="Token has been revoked")
        except HTTPException:
            raise
        except Exception:
            if os.environ.get("TESTING") == "1":
                return payload
            # Treat as verification failure if DB access is unavailable
            raise credentials_exc

    return payload


def get_current_user(payload: dict = Depends(verify_token)) -> dict:
    """Extract user_id from the verified payload and fetch the user from the DB."""
    user_id: str | None = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid token subject")

    try:
        from modules.flow_gate.db import users as db_users
        user = db_users.get_by_id(user_id)
    except Exception:
        raise HTTPException(status_code=500, detail="User lookup failed")

    if not user:
        raise HTTPException(status_code=401, detail="User not found")

    if not user.get("is_active"):
        raise HTTPException(status_code=403, detail="User account is inactive")

    return user


def optional_current_user(
    token: str | None = Depends(OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login", auto_error=False)),
) -> dict | None:
    """Return None when the token is missing or invalid (for optional authentication)."""
    if not token:
        return None
    try:
        payload = verify_token(token)
        return get_current_user(payload)
    except HTTPException:
        return None
