"""System security policy (flowgate.v02.0020, 시안 14 시스템 › 보안).

The values live in system_settings under `security.*` and are enforced where they act:

  tfa_scope / tfa_grace_days   auth_api.login asks a user in scope without 2FA to enrol once the
                               grace period (counted from the policy change or the user's
                               creation, whichever is later) is over.
  recovery_codes               off → no backup codes are issued and /auth/totp/backup is refused.
  password_min_length          auth.password.validate_password.
  lock_attempts / lock_minutes auth_api's failed-login / failed-code lock.
  idle_timeout                 auth.middleware (activity) and auth_api.refresh: a session with no
                               API activity for that long is signed out.
  logout_on_tighten            widening the 2FA scope signs out the newly covered users who have
                               not enrolled yet.

An unset key reads as the behaviour the install had before this screen existed (2FA optional,
12-character passwords, 5 failures → 15 minutes), except idle_timeout which defaults to the
longest choice (7 days).
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

from modules.flow_gate.db import connection as _connection
from modules.flow_gate.db import system_settings as _settings_db

PREFIX = "security."

CHOICES: dict[str, tuple] = {
    "tfa_scope": ("off", "admin", "all"),
    "tfa_grace_days": (0, 7, 14, 30),
    "password_min_length": (8, 10, 12),
    "lock_attempts": (3, 5, 10),
    "lock_minutes": (5, 15, 60),
    "idle_timeout": ("30m", "2h", "8h", "7d"),
}
BOOLEANS = ("recovery_codes", "logout_on_tighten")

DEFAULTS: dict = {
    "tfa_scope": "off",
    "tfa_grace_days": 7,
    "recovery_codes": True,
    "password_min_length": 12,
    "lock_attempts": 5,
    "lock_minutes": 15,
    "idle_timeout": "7d",
    "logout_on_tighten": False,
}

IDLE_SECONDS = {"30m": 30 * 60, "2h": 2 * 3600, "8h": 8 * 3600, "7d": 7 * 86400}
_SCOPE_RANK = {"off": 0, "admin": 1, "all": 2}

_CACHE_TTL = 10.0
_cache_lock = threading.Lock()
_cache: dict = {"at": 0.0, "value": None}


class SecurityPolicyError(Exception):
    def __init__(self, code: str, field: str):
        super().__init__(f"{field}: {code}")
        self.code = code
        self.field = field


def _parse(key: str, raw: Optional[str]):
    if raw is None or raw == "":
        return DEFAULTS[key]
    if key in BOOLEANS:
        return str(raw).strip().lower() in ("1", "true", "yes", "on")
    choices = CHOICES[key]
    if isinstance(choices[0], int):
        try:
            value = int(raw)
        except (TypeError, ValueError):
            return DEFAULTS[key]
    else:
        value = str(raw)
    return value if value in choices else DEFAULTS[key]


def invalidate_cache() -> None:
    with _cache_lock:
        _cache["at"] = 0.0
        _cache["value"] = None


def get_policy(use_cache: bool = True) -> dict:
    now = time.monotonic()
    if use_cache:
        with _cache_lock:
            if _cache["value"] is not None and now - _cache["at"] < _CACHE_TTL:
                return dict(_cache["value"])
    try:
        rows = {r["setting_key"]: r.get("setting_value") for r in _settings_db.list_settings()
                if str(r.get("setting_key", "")).startswith(PREFIX)}
    except Exception:  # noqa: BLE001 — a store that cannot be read enforces the defaults
        rows = {}
    policy = {key: _parse(key, rows.get(PREFIX + key)) for key in DEFAULTS}
    policy["tfa_policy_changed_at"] = rows.get(PREFIX + "tfa_policy_changed_at") or None
    with _cache_lock:
        _cache["at"], _cache["value"] = now, dict(policy)
    return policy


def _validate(updates: dict) -> dict:
    clean = {}
    for key, value in (updates or {}).items():
        if key not in DEFAULTS:
            raise SecurityPolicyError("unknown_key", key)
        if key in BOOLEANS:
            if not isinstance(value, bool):
                raise SecurityPolicyError("invalid_value", key)
            clean[key] = value
            continue
        if value not in CHOICES[key] or isinstance(value, bool):
            raise SecurityPolicyError("invalid_value", key)
        clean[key] = value
    return clean


def _now() -> datetime:
    return datetime.now(timezone.utc)


def parse_time(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace(" ", "T").replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def update_policy(updates: dict, actor: Optional[str] = None) -> dict:
    """Save the given keys. Returns {"policy", "signed_out"}; signed_out counts the users whose
    sessions were ended because the 2FA scope widened with logout_on_tighten on."""
    clean = _validate(updates)
    before = get_policy(use_cache=False)
    for key, value in clean.items():
        stored = ("true" if value else "false") if key in BOOLEANS else str(value)
        _settings_db.set_value(PREFIX + key, stored, "boolean" if key in BOOLEANS else
                               ("integer" if isinstance(value, int) else "string"), updated_by=actor)
    if "tfa_scope" in clean and clean["tfa_scope"] != before["tfa_scope"]:
        _settings_db.set_value(PREFIX + "tfa_policy_changed_at", _now().isoformat(timespec="seconds"),
                               "string", updated_by=actor)
    invalidate_cache()
    after = get_policy(use_cache=False)
    signed_out = 0
    if after["logout_on_tighten"] and _SCOPE_RANK[after["tfa_scope"]] > _SCOPE_RANK[before["tfa_scope"]]:
        signed_out = _sign_out_newly_covered(before["tfa_scope"], after["tfa_scope"])
    return {"policy": after, "signed_out": signed_out}


def _in_scope(scope: str, is_admin: bool) -> bool:
    return scope == "all" or (scope == "admin" and is_admin)


def _sign_out_newly_covered(old_scope: str, new_scope: str) -> int:
    from modules.flow_gate.auth.session_store import revoke_all_sessions

    count = 0
    for row in _connection.get_store()._fetch_all(
        "SELECT user_id, is_admin, totp_secret FROM users WHERE is_active = 1"
    ):
        is_admin = bool(row.get("is_admin"))
        if row.get("totp_secret") or _in_scope(old_scope, is_admin) or not _in_scope(new_scope, is_admin):
            continue
        revoke_all_sessions(row["user_id"], "admin")
        count += 1
    return count


# ── enforcement helpers ──────────────────────────────────────────────────────

def tfa_required(user: dict, policy: Optional[dict] = None) -> bool:
    policy = policy or get_policy()
    return _in_scope(policy["tfa_scope"], bool(user.get("is_admin")))


def tfa_deadline(user: dict, policy: Optional[dict] = None) -> Optional[datetime]:
    """When a user in scope must have enrolled; None when the user is not in scope."""
    policy = policy or get_policy()
    if not tfa_required(user, policy):
        return None
    starts = [t for t in (parse_time(policy.get("tfa_policy_changed_at")), parse_time(user.get("created_at"))) if t]
    start = max(starts) if starts else _now()
    return start + timedelta(days=int(policy["tfa_grace_days"]))


def tfa_enrollment_due(user: dict, policy: Optional[dict] = None) -> bool:
    if user.get("totp_secret"):
        return False
    deadline = tfa_deadline(user, policy)
    return deadline is not None and _now() >= deadline


def lock_rule() -> tuple[int, int]:
    policy = get_policy()
    return int(policy["lock_attempts"]), int(policy["lock_minutes"])


def password_min_length() -> int:
    return int(get_policy()["password_min_length"])


def recovery_codes_enabled() -> bool:
    return bool(get_policy()["recovery_codes"])


def idle_seconds() -> int:
    return IDLE_SECONDS[get_policy()["idle_timeout"]]


def coverage() -> list[dict]:
    """Every user who can sign in (active or invited) with admin flag and 2FA state — the people
    row on the 보안 screen."""
    from modules.flow_gate.settings.workspace_service import user_state

    rows = _connection.get_store()._fetch_all(
        "SELECT user_id, username, email, is_admin, is_active, totp_secret, invited_at, last_login_at "
        "FROM users WHERE is_active = 1 ORDER BY is_admin DESC, username"
    )
    return [
        {
            "user_id": r["user_id"],
            "username": r["username"],
            "is_admin": bool(r.get("is_admin")),
            "tfa_enabled": bool(r.get("totp_secret")),
            "state": user_state(r),
        }
        for r in rows
    ]
