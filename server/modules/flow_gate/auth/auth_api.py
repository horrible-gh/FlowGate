"""Authentication API router (D012 r1 + D016 r1 aligned).

Endpoints:
  POST   /login               Primary ID/PW authentication
  POST   /totp/verify         Secondary TOTP authentication
  POST   /totp/backup         Backup code authentication
  POST   /totp/setup          TOTP registration (QR + backup codes)
  POST   /refresh             Renew access_token
  POST   /logout              Logout
  POST   /password/change     Change password
  GET    /me                  Current user info
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from slowapi import Limiter
from slowapi.util import get_remote_address

from modules.flow_gate.db import users as db_users

from .backup_codes import generate_codes, store_codes, verify_backup_code
from .jwt_service import (
    create_access_token,
    create_refresh_token,
    create_temp_token,
    decode_token,
    decode_token_no_verify_exp,
)
from .middleware import get_current_user, verify_token
from .password import hash_password, validate_password, verify_password
from .token_store import (
    blacklist_token,
    get_refresh_token,
    is_blacklisted,
    revoke_all_refresh_tokens,
    revoke_refresh_token,
    rotate_refresh_token,
    store_refresh_token,
)
from .totp_service import (
    TOTP_LOCK_MAX_ATTEMPTS,
    TOTP_LOCK_MINUTES,
    decrypt_totp_secret,
    encrypt_totp_secret,
    generate_totp_secret,
    get_totp_provisioning_uri,
    verify_totp_code,
)

limiter = Limiter(key_func=get_remote_address)
router = APIRouter()


# ── Helpers ─────────────────────────────────────────────────────────────────

def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _get_user_roles(user_id: str) -> list[str]:
    """Return a list of all role names assigned to the given user_id."""
    try:
        from modules.flow_gate.db.connection import get_store
        rows = get_store()._fetch_all(
            "SELECT DISTINCT r.role_name FROM roles r "
            "JOIN user_project_roles upr ON r.role_id = upr.role_id "
            "WHERE upr.user_id = ?",
            [user_id],
        )
        return [r["role_name"] for r in rows]
    except Exception:
        return []


def _build_token_response(user: dict) -> dict:
    """Build the token response (access + refresh tokens and user info) on successful login."""
    user_id = user["user_id"]
    roles = _get_user_roles(user_id)
    is_admin = bool(user.get("is_admin", 0))

    access_token, access_jti = create_access_token(
        user_id=user_id,
        username=user["username"],
        roles=roles,
        is_admin=is_admin,
    )
    refresh_token, refresh_jti, expires_at = create_refresh_token(user_id=user_id)

    store_refresh_token(refresh_jti, user_id, expires_at)

    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
        "first_login_required": bool(user.get("first_login_required")),
        "user": {
            "user_id": user_id,
            "username": user["username"],
            "email": user["email"],
        },
    }


def _check_totp_lock(user: dict) -> None:
    """Check TOTP lock state. Raises HTTP 423 if account is currently locked."""
    locked_until_str = user.get("totp_locked_until")
    if locked_until_str:
        try:
            locked_until = datetime.fromisoformat(locked_until_str)
            if locked_until.tzinfo is None:
                locked_until = locked_until.replace(tzinfo=timezone.utc)
            if _now_utc() < locked_until:
                raise HTTPException(
                    status_code=423,
                    detail={
                        "code": "account_locked",
                        "locked_until": locked_until_str,
                    },
                )
        except HTTPException:
            raise
        except Exception:
            pass


def _increment_totp_fail(user: dict) -> None:
    """Increment the TOTP failure counter. Locks the account for 15 minutes after 5 attempts."""
    from modules.flow_gate.db.connection import now_iso
    from datetime import timedelta

    user_id = user["user_id"]
    count = (user.get("totp_failed_count") or 0) + 1

    updates: dict = {"totp_failed_count": count}
    if count >= TOTP_LOCK_MAX_ATTEMPTS:
        locked_until = (_now_utc() + timedelta(minutes=TOTP_LOCK_MINUTES)).isoformat(
            timespec="seconds"
        )
        updates["totp_locked_until"] = locked_until

    db_users.update(user_id, updates)


def _reset_totp_lock(user_id: str) -> None:
    """Reset the TOTP failure counter on successful verification."""
    db_users.update(user_id, {"totp_failed_count": 0, "totp_locked_until": None})


LOGIN_LOCK_MAX_ATTEMPTS = 5
LOGIN_LOCK_MINUTES = 15


def _check_login_lock(user: dict) -> None:
    """Check login_locked_until — raises HTTP 423 if account is currently locked."""
    locked_until_str = user.get("login_locked_until")
    if locked_until_str:
        try:
            locked_until = datetime.fromisoformat(locked_until_str)
            if locked_until.tzinfo is None:
                locked_until = locked_until.replace(tzinfo=timezone.utc)
            if _now_utc() < locked_until:
                raise HTTPException(
                    status_code=423,
                    detail={
                        "code": "account_locked",
                        "locked_until": locked_until_str,
                    },
                )
        except HTTPException:
            raise
        except Exception:
            pass


def _increment_login_fail(user: dict) -> None:
    """Increment login_failed_count — locks the account for 15 minutes when LOGIN_LOCK_MAX_ATTEMPTS is reached."""
    from datetime import timedelta

    user_id = user["user_id"]
    count = (user.get("login_failed_count") or 0) + 1

    updates: dict = {"login_failed_count": count}
    if count >= LOGIN_LOCK_MAX_ATTEMPTS:
        locked_until = (_now_utc() + timedelta(minutes=LOGIN_LOCK_MINUTES)).isoformat(
            timespec="seconds"
        )
        updates["login_locked_until"] = locked_until

    db_users.update(user_id, updates)


def _reset_login_lock(user_id: str) -> None:
    """Reset the login failure counter on successful login."""
    db_users.update(user_id, {"login_failed_count": 0, "login_locked_until": None})


# ── Request models ───────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    username: str
    password: str
    locale: str = "en"


class TotpVerifyRequest(BaseModel):
    temp_token: str
    code: str


class TotpBackupRequest(BaseModel):
    temp_token: str
    backup_code: str


class RefreshRequest(BaseModel):
    refresh_token: str


class LogoutRequest(BaseModel):
    refresh_token: Optional[str] = None


class PasswordChangeRequest(BaseModel):
    current_password: Optional[str] = None
    new_password: str


# ── Endpoints ────────────────────────────────────────────────────────────────

@router.post("/login")
async def login(request: Request, body: LoginRequest):
    """Primary ID/PW authentication.

    Response:
    - TOTP-enrolled user: { totp_required: true, temp_token }
    - TOTP-not-enrolled user: { access_token, refresh_token, token_type, user }
    """
    # Check maintenance mode
    try:
        from config import settings  # type: ignore[import]
        if settings.MAINTENANCE_MODE:
            return {"maintenance": True, "message": "Server maintenance in progress."}
    except Exception:
        pass

    # Look up user
    user = db_users.get_by_username(body.username)
    if not user and "@" in body.username:
        user = db_users.get_by_email(body.username)
    if not user:
        raise HTTPException(status_code=400, detail="invalid_credentials")

    # ① Check lock (before password verification)
    _check_login_lock(user)

    if not verify_password(body.password, user.get("password", "")):
        _increment_login_fail(user)  # ② Increment counter on failure
        raise HTTPException(status_code=400, detail="invalid_credentials")

    _reset_login_lock(user["user_id"])  # ③ Reset on success

    if not user.get("is_active"):
        raise HTTPException(status_code=403, detail="account_inactive")

    user_id = user["user_id"]

    # Check if TOTP is configured (require 2FA only when enrolled)
    encrypted_secret = user.get("totp_secret")
    if encrypted_secret:
        temp_token, _ = create_temp_token(user_id)
        return {"totp_required": True, "temp_token": temp_token}

    # Issue tokens immediately when TOTP is not enrolled (including admin users)
    # TOTP setup is done by the user via /auth/totp/setup after login
    return _build_token_response(user)


@router.post("/totp/verify")
async def verify_totp(request: Request, body: TotpVerifyRequest):
    """Secondary TOTP code authentication."""
    credentials_exc = HTTPException(status_code=401, detail="token_expired")

    try:
        payload = decode_token(body.temp_token)
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="token_expired")
    except jwt.InvalidTokenError:
        raise credentials_exc

    if payload.get("type") != "temp" or not payload.get("totp_pending"):
        raise credentials_exc

    user_id: str | None = payload.get("sub")
    if not user_id:
        raise credentials_exc

    user = db_users.get_by_id(user_id)
    if not user:
        raise credentials_exc

    _check_totp_lock(user)

    encrypted_secret = user.get("totp_secret")
    if not encrypted_secret:
        raise HTTPException(status_code=400, detail="totp_not_configured")

    if not verify_totp_code(encrypted_secret, body.code):
        _increment_totp_fail(user)
        raise HTTPException(status_code=401, detail="invalid_code")

    _reset_totp_lock(user_id)
    return _build_token_response(user)


@router.post("/totp/backup")
async def verify_totp_backup(request: Request, body: TotpBackupRequest):
    """Secondary authentication using a backup code."""
    credentials_exc = HTTPException(status_code=401, detail="token_expired")

    try:
        payload = decode_token(body.temp_token)
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="token_expired")
    except jwt.InvalidTokenError:
        raise credentials_exc

    if payload.get("type") != "temp" or not payload.get("totp_pending"):
        raise credentials_exc

    user_id: str | None = payload.get("sub")
    if not user_id:
        raise credentials_exc

    user = db_users.get_by_id(user_id)
    if not user:
        raise credentials_exc

    _check_totp_lock(user)

    if not verify_backup_code(user_id, body.backup_code):
        _increment_totp_fail(user)
        raise HTTPException(status_code=401, detail="invalid_backup_code")

    _reset_totp_lock(user_id)
    return _build_token_response(user)


@router.post("/totp/setup")
async def setup_totp(request: Request, current_user: dict = Depends(get_current_user)):
    """Register TOTP: returns QR URI, masked secret, and backup codes."""
    user_id = current_user["user_id"]
    username = current_user.get("username", user_id)

    plain_secret = generate_totp_secret()
    encrypted_secret = encrypt_totp_secret(plain_secret)

    # Save secret
    db_users.update(user_id, {"totp_secret": encrypted_secret})

    # Generate and store backup codes
    codes = generate_codes()
    store_codes(user_id, codes)

    qr_uri = get_totp_provisioning_uri(plain_secret, username)
    secret_masked = plain_secret[:4] + "****"

    return {
        "qr_uri": qr_uri,
        "backup_codes": codes,
        "secret_masked": secret_masked,
    }


@router.post("/refresh")
async def refresh_token(request: Request, body: RefreshRequest):
    """Refresh access_token using refresh_token (token rotation + reuse detection)."""
    credentials_exc = HTTPException(
        status_code=401,
        detail="Invalid authentication credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    try:
        payload = decode_token(body.refresh_token)
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token has expired")
    except jwt.InvalidTokenError:
        raise credentials_exc

    if payload.get("type") != "refresh":
        raise credentials_exc

    jti: str | None = payload.get("jti")
    user_id: str | None = payload.get("sub")
    if not jti or not user_id:
        raise credentials_exc

    row = get_refresh_token(jti)
    if not row:
        raise credentials_exc

    # Reuse detection: refresh attempt with an already-revoked token
    if row.get("revoked_at"):
        revoke_all_refresh_tokens(user_id)
        raise HTTPException(status_code=401, detail="Token reuse detected. All sessions revoked.")

    # Expiry check based on DB expires_at
    try:
        db_expires = datetime.fromisoformat(row["expires_at"])
        if db_expires.tzinfo is None:
            db_expires = db_expires.replace(tzinfo=timezone.utc)
        if _now_utc() > db_expires:
            revoke_refresh_token(jti)
            raise HTTPException(status_code=401, detail="Token has expired")
    except HTTPException:
        raise
    except Exception:
        raise credentials_exc

    user = db_users.get_by_id(user_id)
    if not user or not user.get("is_active"):
        raise credentials_exc

    roles = _get_user_roles(user_id)
    is_admin = bool(user.get("is_admin", 0))
    new_access_token, _ = create_access_token(
        user_id=user_id,
        username=user["username"],
        roles=roles,
        is_admin=is_admin,
    )

    # Refresh token rotation (inherit expiry time)
    new_refresh_token, new_jti, _ = create_refresh_token(
        user_id=user_id,
        expires_at=db_expires,
    )
    rotate_refresh_token(jti, new_jti, user_id, db_expires)

    return {
        "access_token": new_access_token,
        "refresh_token": new_refresh_token,
        "token_type": "bearer",
    }


@router.post("/logout")
async def logout(
    request: Request,
    body: LogoutRequest,
    payload: dict = Depends(verify_token),
):
    """Logout: blacklist access_token and revoke refresh_token."""
    jti = payload.get("jti")
    user_id = payload.get("sub")
    exp = payload.get("exp", 0)

    if jti and user_id:
        blacklist_token(jti, user_id, exp)

    # Revoke refresh_token
    if body.refresh_token:
        try:
            ref_payload = decode_token_no_verify_exp(body.refresh_token)
            ref_jti = ref_payload.get("jti")
            if ref_jti:
                revoke_refresh_token(ref_jti)
        except Exception:
            pass

    return {"message": "Logged out successfully"}


@router.post("/password/change")
async def change_password(
    request: Request,
    body: PasswordChangeRequest,
    current_user: dict = Depends(get_current_user),
):
    """Change password. current_password may be omitted when a forced change (first_login_required) is in effect."""
    user_id = current_user["user_id"]
    is_forced = bool(current_user.get("first_login_required"))

    if not is_forced:
        if not body.current_password:
            raise HTTPException(status_code=400, detail="current_password_required")
        if not verify_password(body.current_password, current_user.get("password", "")):
            raise HTTPException(status_code=401, detail="current_password_incorrect")

    # New password must not match the current password
    if body.current_password and verify_password(body.new_password, current_user.get("password", "")):
        raise HTTPException(status_code=422, detail="same_as_current")

    # Validate password policy
    violations = validate_password(body.new_password)
    if violations:
        raise HTTPException(
            status_code=400,
            detail={"code": "invalid_password_policy", "violations": violations},
        )

    new_hash = hash_password(body.new_password)
    updates: dict = {
        "password": new_hash,
        "first_login_required": 0,
    }
    db_users.update(user_id, updates)

    # Revoke all existing refresh tokens
    revoke_all_refresh_tokens(user_id)

    return {
        "message": "Password changed successfully",
        "first_login_required": False,
    }


@router.get("/me")
async def get_me(current_user: dict = Depends(get_current_user)):
    """Return info for the currently authenticated user."""
    user_id = current_user["user_id"]
    roles = _get_user_roles(user_id)
    return {
        "user_id": user_id,
        "username": current_user.get("username"),
        "email": current_user.get("email"),
        "is_admin": bool(current_user.get("is_admin")),
        "first_login_required": bool(current_user.get("first_login_required")),
        "roles": roles,
    }
