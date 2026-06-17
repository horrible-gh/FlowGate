"""Token store: token_blacklist / refresh_tokens DB operations.

Responsible for access_token blacklist and refresh_token rotation/reuse detection.
"""
from __future__ import annotations
from datetime import datetime, timezone

from modules.flow_gate.db.connection import get_store, now_iso


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


# ── Token blacklist ──────────────────────────────────────────────────────────

def blacklist_token(jti: str, user_id: str, exp_timestamp: int) -> None:
    """Register access_token jti in the blacklist."""
    expires_at = datetime.fromtimestamp(exp_timestamp, timezone.utc)
    get_store()._execute(
        "INSERT OR IGNORE INTO token_blacklist (jti, user_id, revoked_at, expires_at) "
        "VALUES (?, ?, ?, ?)",
        [jti, user_id, now_iso(), _iso(expires_at)],
    )


def is_blacklisted(jti: str) -> bool:
    """Check if jti is in the blacklist."""
    row = get_store()._fetch_one(
        "SELECT jti FROM token_blacklist WHERE jti = ?", [jti]
    )
    return row is not None


# ── refresh_tokens ───────────────────────────────────────────────────────────

def store_refresh_token(jti: str, user_id: str, expires_at: datetime) -> None:
    """Store a new refresh_token in the DB."""
    get_store()._execute(
        "INSERT INTO refresh_tokens (jti, user_id, issued_at, expires_at) "
        "VALUES (?, ?, ?, ?)",
        [jti, user_id, now_iso(), _iso(expires_at)],
    )


def get_refresh_token(jti: str) -> dict | None:
    """Retrieve refresh_token record by jti."""
    return get_store()._fetch_one(
        "SELECT * FROM refresh_tokens WHERE jti = ?", [jti]
    )


def revoke_refresh_token(jti: str) -> None:
    """Revoke a single refresh_token."""
    get_store()._execute(
        "UPDATE refresh_tokens SET revoked_at = ? WHERE jti = ?",
        [now_iso(), jti],
    )


def revoke_all_refresh_tokens(user_id: str) -> None:
    """Revoke all active refresh_tokens for a user_id (terminate all sessions when reuse is detected)."""
    get_store()._execute(
        "UPDATE refresh_tokens SET revoked_at = ? "
        "WHERE user_id = ? AND revoked_at IS NULL",
        [now_iso(), user_id],
    )


def rotate_refresh_token(
    old_jti: str,
    new_jti: str,
    user_id: str,
    new_expires_at: datetime,
) -> None:
    """Revoke the old jti (recording replaced_by) and insert the new jti.

    Because replaced_by is a FK to refresh_tokens(jti), insert the new token first
    then update the existing token.
    """
    now = now_iso()
    get_store()._execute(
        "INSERT INTO refresh_tokens (jti, user_id, issued_at, expires_at) "
        "VALUES (?, ?, ?, ?)",
        [new_jti, user_id, now, _iso(new_expires_at)],
    )
    get_store()._execute(
        "UPDATE refresh_tokens SET revoked_at = ?, replaced_by = ? WHERE jti = ?",
        [now, new_jti, old_jti],
    )
