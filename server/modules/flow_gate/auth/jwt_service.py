"""JWT service: token creation and verification (HS256).

Token types:
- access : sub + username + roles + jti, 30 minutes
- refresh: sub + jti, 14 days
- temp   : sub + totp_pending=True + jti, 5 minutes
"""
from __future__ import annotations
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt

SECRET_KEY: str = os.environ.get("SECRET_KEY", "")  # fallback if config import is not available
ALGORITHM = "HS256"
# Default fallback only. The effective access-token lifetime is resolved at call time
# from config.settings.ACCESS_TOKEN_EXPIRE_MINUTES so the `.env` value is the single
# source of truth (group 0021 / NR0003: this constant used to be authoritative, which
# silently ignored the operator's .env override).
ACCESS_TOKEN_EXPIRE_MINUTES = 30
REFRESH_TOKEN_EXPIRE_DAYS = 14
TOTP_PENDING_EXPIRE_MINUTES = 5


def _get_secret() -> str:
    if SECRET_KEY:
        return SECRET_KEY
    # Refer to config at runtime (delayed loading to avoid circular imports)
    try:
        from config import settings  # type: ignore[import]
        return settings.SECRET_KEY
    except Exception:
        raise RuntimeError("SECRET_KEY environment variable or config.settings.SECRET_KEY is required.")


def get_access_token_expire_minutes() -> int:
    """Resolve the access-token lifetime (minutes) from a single source of truth.

    Priority: config.settings.ACCESS_TOKEN_EXPIRE_MINUTES → ACCESS_TOKEN_EXPIRE_MINUTES
    env var → module default. Resolved at call time so changing `.env` takes effect
    without code edits, and so tests can override via the environment.
    """
    try:
        from config import settings  # type: ignore[import]
        return int(settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    except Exception:
        try:
            return int(os.environ.get("ACCESS_TOKEN_EXPIRE_MINUTES", ACCESS_TOKEN_EXPIRE_MINUTES))
        except (TypeError, ValueError):
            return ACCESS_TOKEN_EXPIRE_MINUTES


def create_access_token(
    user_id: str,
    username: str,
    roles: list[str],
    is_admin: bool = False,
    expires_delta: Optional[timedelta] = None,
) -> tuple[str, str]:
    """Create access token. Returns: (token, jti)"""
    jti = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    exp = now + (expires_delta or timedelta(minutes=get_access_token_expire_minutes()))
    payload = {
        "sub": user_id,
        "username": username,
        "roles": roles,
        "is_admin": is_admin,
        "jti": jti,
        "type": "access",
        "iat": int(now.timestamp()),
        "exp": int(exp.timestamp()),
    }
    token = jwt.encode(payload, _get_secret(), algorithm=ALGORITHM)
    return token, jti


def create_refresh_token(
    user_id: str,
    expires_at: Optional[datetime] = None,
) -> tuple[str, str, datetime]:
    """Create refresh token. Returns: (token, jti, expires_at)"""
    jti = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    if expires_at is None:
        expires_at = now + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    payload = {
        "sub": user_id,
        "jti": jti,
        "type": "refresh",
        "iat": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
    }
    token = jwt.encode(payload, _get_secret(), algorithm=ALGORITHM)
    return token, jti, expires_at


def create_temp_token(user_id: str) -> tuple[str, str]:
    """Temporary token for TOTP pending (5 minutes). Returns: (token, jti)"""
    jti = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    exp = now + timedelta(minutes=TOTP_PENDING_EXPIRE_MINUTES)
    payload = {
        "sub": user_id,
        "jti": jti,
        "type": "temp",
        "totp_pending": True,
        "iat": int(now.timestamp()),
        "exp": int(exp.timestamp()),
    }
    token = jwt.encode(payload, _get_secret(), algorithm=ALGORITHM)
    return token, jti


def decode_token(token: str) -> dict:
    """Decode JWT. Raises jwt.ExpiredSignatureError / jwt.InvalidTokenError on expiration or signature errors."""
    return jwt.decode(token, _get_secret(), algorithms=[ALGORITHM])


def decode_token_no_verify_exp(token: str) -> dict:
    """Decode without verifying expiration (for extracting claims from expired tokens, e.g., during logout handling)."""
    return jwt.decode(
        token,
        _get_secret(),
        algorithms=[ALGORITHM],
        options={"verify_exp": False},
    )
