"""System › 사용자 (flowgate.v02.0020, 시안 13).

Users are system-wide identities. This service adds what the screen needs on top of the older
user_admin_service: the state each row shows (active / invited / disabled), 2FA, workspaces and
last sign-in, invitations, and the "last system administrator" guard.

Invitations: the server has no mail transport wired (server/util/mail.py is not initialised),
so an invitation is a one-time link the administrator hands over. Only its SHA-256 is stored;
accepting it sets the password and clears the invitation (auth_api /auth/invite/accept).
"""
from __future__ import annotations

import hashlib
import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from modules.flow_gate.auth.password import hash_password
from modules.flow_gate.db import connection as _connection
from modules.flow_gate.db import users as _users_db
from modules.flow_gate.db import workspaces as _ws_db
from modules.flow_gate.rbac import role_service
from modules.flow_gate.settings import workspace_service
from modules.flow_gate.settings.workspace_service import WorkspaceError, user_state

INVITE_DAYS = 7
# Reserved accounts that are not people (038: the login-disabled owner of AI question containers).
RESERVED_USER_IDS = ("u-system",)
NAME_MAX = 100
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class SystemUserError(Exception):
    def __init__(self, status: int, code: str):
        super().__init__(code)
        self.status = status
        self.code = code


def _store():
    return _connection.get_store()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _new_invite() -> tuple[str, dict]:
    token = secrets.token_urlsafe(32)
    return token, {
        "invited_at": _now().isoformat(timespec="seconds"),
        "invite_token_hash": _hash_token(token),
        "invite_expires_at": (_now() + timedelta(days=INVITE_DAYS)).isoformat(timespec="seconds"),
    }


def invite_path(token: str) -> str:
    return f"/login?invite={token}"


def _view(row: dict, workspaces: list[dict]) -> dict:
    return {
        "user_id": row["user_id"],
        "username": row["username"],
        "email": row.get("email"),
        "is_admin": bool(row.get("is_admin")),
        "state": user_state(row),
        "tfa_enabled": bool(row.get("totp_secret")),
        "workspaces": workspaces,
        "last_login_at": row.get("last_login_at"),
        "created_at": row.get("created_at"),
    }


def list_users() -> dict:
    workspace_service.ensure_default_workspace()
    rows = _store()._fetch_all(
        "SELECT user_id, username, email, is_admin, is_active, totp_secret, invited_at, last_login_at, created_at "
        "FROM users WHERE user_id NOT IN (" + ", ".join("?" for _ in RESERVED_USER_IDS) + ") "
        "ORDER BY created_at, username",
        list(RESERVED_USER_IDS),
    )
    by_user: dict[str, list[dict]] = {}
    for m in _ws_db.all_memberships():
        by_user.setdefault(m["user_id"], []).append(
            {"workspace_id": m["workspace_id"], "name": m["name"], "role": m["role"]}
        )
    users = [_view(r, by_user.get(r["user_id"], [])) for r in rows]
    summary = {
        "total": len(users),
        "active": sum(1 for u in users if u["state"] == "active"),
        "invited": sum(1 for u in users if u["state"] == "invited"),
        "disabled": sum(1 for u in users if u["state"] == "disabled"),
        "admins": sum(1 for u in users if u["is_admin"] and u["state"] != "disabled"),
        "tfa_missing": sum(1 for u in users if not u["tfa_enabled"] and u["state"] != "disabled"),
        "tfa_enabled": sum(1 for u in users if u["tfa_enabled"] and u["state"] != "disabled"),
        "no_workspace": sum(1 for u in users if not u["workspaces"] and u["state"] != "disabled"),
    }
    workspaces = [
        {"workspace_id": w["workspace_id"], "name": w["name"]}
        for w in _ws_db.list_all() if w["status"] == "active"
    ]
    return {"users": users, "summary": summary, "workspaces": workspaces}


def get_user_view(user_id: str) -> dict:
    row = _users_db.get_by_id(user_id)
    if not row or user_id in RESERVED_USER_IDS:
        raise SystemUserError(404, "user_not_found")
    workspaces = [
        {"workspace_id": m["workspace_id"], "name": m["name"], "role": m["role"]}
        for m in _ws_db.memberships_for_user(user_id)
    ]
    return _view(row, workspaces)


def _active_admin_count() -> int:
    row = _store()._fetch_one("SELECT COUNT(*) AS cnt FROM users WHERE is_admin = 1 AND is_active = 1")
    return int(row["cnt"]) if row else 0


def _is_last_admin(row: dict) -> bool:
    return bool(row.get("is_admin")) and bool(row.get("is_active")) and _active_admin_count() <= 1


def create_user(actor: dict, name: str, email: str, start: str, is_admin: bool,
                workspace_id: Optional[str] = None) -> dict:
    """start 'invite' → an invitation link; 'password' → a temporary password that must be
    changed at first sign-in. Returns {"user", "invite_path"?, "temp_password"?}."""
    display = re.sub(r"\s+", " ", (name or "").strip())
    mail = (email or "").strip()
    if not display:
        raise SystemUserError(422, "name_required")
    if len(display) > NAME_MAX:
        raise SystemUserError(422, "name_too_long")
    if not _EMAIL_RE.match(mail):
        raise SystemUserError(422, "email_invalid")
    if start not in ("invite", "password"):
        raise SystemUserError(422, "start_invalid")
    if _users_db.get_by_username(display):
        raise SystemUserError(409, "username_already_exists")
    if _users_db.get_by_email(mail):
        raise SystemUserError(409, "email_already_exists")
    if workspace_id:
        ws = _ws_db.get(workspace_id)
        if not ws or ws["status"] != "active":
            raise SystemUserError(422, "workspace_invalid")

    uid = f"usr_{uuid.uuid4().hex[:16]}"
    temp_password = secrets.token_urlsafe(12)
    token, invite = _new_invite() if start == "invite" else (None, {})
    system_role = "role_admin" if is_admin else "role_viewer"
    with _store().transaction():
        _users_db.create({
            "user_id": uid,
            "username": display,
            "email": mail,
            # An invited user has no usable password until the invitation is accepted.
            "password": hash_password(temp_password if start == "password" else secrets.token_urlsafe(32)),
            "is_active": 1,
            "is_admin": int(bool(is_admin)),
            "first_login_required": 1 if start == "password" else 0,
        })
        if invite:
            _users_db.update(uid, invite)
        role_service.assign_role(uid, role_service.SYSTEM_PROJECT, system_role, granted_by=actor.get("user_id"))
        if workspace_id:
            try:
                workspace_service.add_user_to_workspace(workspace_id, uid, "member", actor.get("user_id"))
            except WorkspaceError as exc:
                raise SystemUserError(exc.status, exc.code) from None
    result: dict = {"user": get_user_view(uid)}
    if token:
        result["invite_path"] = invite_path(token)
        result["invite_expires_at"] = invite["invite_expires_at"]
    else:
        result["temp_password"] = temp_password
    return result


def set_admin(actor: dict, user_id: str, is_admin: bool) -> dict:
    row = _users_db.get_by_id(user_id)
    if not row:
        raise SystemUserError(404, "user_not_found")
    if not is_admin and _is_last_admin(row):
        raise SystemUserError(409, "last_admin")
    with _store().transaction():
        _users_db.update(user_id, {"is_admin": int(bool(is_admin))})
        role_service.assign_role(user_id, role_service.SYSTEM_PROJECT,
                                 "role_admin" if is_admin else "role_viewer", granted_by=actor.get("user_id"))
    return get_user_view(user_id)


def disable_user(actor: dict, user_id: str) -> dict:
    """Disable (or cancel an invitation): sign-in blocked, sessions ended, data kept."""
    from modules.flow_gate.auth.session_store import revoke_all_sessions

    row = _users_db.get_by_id(user_id)
    if not row:
        raise SystemUserError(404, "user_not_found")
    if _is_last_admin(row):
        raise SystemUserError(409, "last_admin")
    if actor.get("user_id") == user_id:
        raise SystemUserError(409, "cannot_disable_self")
    _users_db.update(user_id, {"is_active": 0, "invite_token_hash": None, "invite_expires_at": None})
    revoke_all_sessions(user_id, "admin")
    return get_user_view(user_id)


def enable_user(actor: dict, user_id: str) -> dict:
    row = _users_db.get_by_id(user_id)
    if not row:
        raise SystemUserError(404, "user_not_found")
    _users_db.update(user_id, {"is_active": 1})
    return get_user_view(user_id)


def resend_invite(actor: dict, user_id: str) -> dict:
    row = _users_db.get_by_id(user_id)
    if not row:
        raise SystemUserError(404, "user_not_found")
    if user_state(row) != "invited":
        raise SystemUserError(409, "not_invited")
    token, invite = _new_invite()
    _users_db.update(user_id, invite)
    return {"user": get_user_view(user_id), "invite_path": invite_path(token),
            "invite_expires_at": invite["invite_expires_at"]}


def reset_password(actor: dict, user_id: str) -> dict:
    from modules.flow_gate.auth.session_store import revoke_all_sessions

    row = _users_db.get_by_id(user_id)
    if not row:
        raise SystemUserError(404, "user_not_found")
    temp_password = secrets.token_urlsafe(12)
    _users_db.update(user_id, {"password": hash_password(temp_password), "first_login_required": 1})
    revoke_all_sessions(user_id, "admin")
    return {"user": get_user_view(user_id), "temp_password": temp_password}


def reset_tfa(actor: dict, user_id: str) -> dict:
    """Delete the 2FA registration; the user enrols again at the next sign-in when the policy
    requires it."""
    from modules.flow_gate.db import totp_backup_codes as _backup_db

    row = _users_db.get_by_id(user_id)
    if not row:
        raise SystemUserError(404, "user_not_found")
    _users_db.update(user_id, {"totp_secret": None, "totp_failed_count": 0, "totp_locked_until": None})
    _backup_db.delete_all(user_id)
    return get_user_view(user_id)


# ── invitation acceptance (public, used by /auth/invite) ─────────────────────

def find_invited(token: str) -> Optional[dict]:
    if not token:
        return None
    row = _store()._fetch_one("SELECT * FROM users WHERE invite_token_hash = ?", [_hash_token(token)])
    if not row or not row.get("is_active"):
        return None
    expires = _parse(row.get("invite_expires_at"))
    if expires is None or _now() > expires:
        return None
    return row


def accept_invite(token: str, password: str) -> dict:
    from modules.flow_gate.auth.password import validate_password

    row = find_invited(token)
    if not row:
        raise SystemUserError(404, "invite_invalid")
    violations = validate_password(password)
    if violations:
        raise SystemUserError(400, "invalid_password_policy")
    _users_db.update(row["user_id"], {
        "password": hash_password(password),
        "first_login_required": 0,
        "invite_token_hash": None,
        "invite_expires_at": None,
    })
    return {"username": row["username"], "email": row.get("email")}


def _parse(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace(" ", "T"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
