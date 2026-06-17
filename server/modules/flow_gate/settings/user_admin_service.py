"""User administration service (D018 r1 §B).

admin: full CRUD + role assignment
manager: can only view members in their own projects (PM decision #2)
"""
from __future__ import annotations

import uuid

from modules.flow_gate.auth.backup_codes import count_unused, generate_codes, store_codes
from modules.flow_gate.auth.password import hash_password
from modules.flow_gate.db import users as _db_users
from modules.flow_gate.db.connection import get_store, now_iso



def list_users_for_admin(
    search: str | None = None,
    role: str | None = None,
    is_active: int | None = None,
    page: int = 1,
    per_page: int = 20,
) -> dict:
    """Admin: list all users."""
    store = get_store()
    sql = "SELECT u.* FROM users u WHERE 1=1"
    params: list = []
    if search:
        sql += " AND (u.username LIKE ? OR u.email LIKE ?)"
        params += [f"%{search}%", f"%{search}%"]
    if is_active is not None:
        sql += " AND u.is_active = ?"
        params.append(is_active)
    if role:
        sql += (
            " AND EXISTS (SELECT 1 FROM user_project_roles upr"
            " WHERE upr.user_id = u.user_id AND upr.role_id = ?)"
        )
        params.append(role)

    count_sql = f"SELECT COUNT(*) as cnt FROM ({sql}) t"
    total_row = store._fetch_one(count_sql, params)
    total = total_row["cnt"] if total_row else 0
    offset = (page - 1) * per_page
    sql += " ORDER BY u.created_at DESC LIMIT ? OFFSET ?"
    items = store._fetch_all(sql, [*params, per_page, offset])
    return {"total": total, "page": page, "per_page": per_page, "items": _sanitize_list(items)}



def list_users_for_manager(manager_id: str, page: int = 1, per_page: int = 20) -> dict:
    """Manager: list only members in the manager's own projects."""
    store = get_store()
    sql = (
        "SELECT DISTINCT u.* FROM users u"
        " INNER JOIN user_project_roles upr ON upr.user_id = u.user_id"
        " WHERE upr.project_id IN ("
        "   SELECT project_id FROM user_project_roles"
        "   WHERE user_id = ? AND project_id != '__SYSTEM__'"
        " )"
    )
    params = [manager_id]
    count_sql = f"SELECT COUNT(*) as cnt FROM ({sql}) t"
    total_row = store._fetch_one(count_sql, params)
    total = total_row["cnt"] if total_row else 0
    offset = (page - 1) * per_page
    full_sql = sql + " ORDER BY u.created_at DESC LIMIT ? OFFSET ?"
    items = store._fetch_all(full_sql, [*params, per_page, offset])
    return {"total": total, "page": page, "per_page": per_page, "items": _sanitize_list(items)}



def get_user(user_id: str) -> dict | None:
    row = _db_users.get_by_id(user_id)
    return _sanitize(row) if row else None



def create_user(data: dict, created_by: str | None = None) -> dict:
    uid = f"usr_{uuid.uuid4().hex[:16]}"
    row = _db_users.create(
        {
            "user_id": uid,
            "username": data["username"],
            "email": data["email"],
            "password": hash_password(data["password"]),
            "is_active": data.get("is_active", 1),
            "is_admin": data.get("is_admin", 0),
            "first_login_required": data.get("first_login_required", 1),
        }
    )
    return _sanitize(row)



def update_user(user_id: str, data: dict) -> dict | None:
    allowed = {"username", "email", "is_active", "is_admin", "first_login_required", "totp_secret", "totp_locked_until", "totp_failed_count", "password"}
    updates = {k: v for k, v in data.items() if k in allowed}
    if not updates:
        return get_user(user_id)
    row = _db_users.update(user_id, updates)
    return _sanitize(row) if row else None



def deactivate_user(user_id: str) -> dict | None:
    """Soft delete — is_active=0."""
    row = _db_users.update(user_id, {"is_active": 0})
    return _sanitize(row) if row else None



def reset_totp(user_id: str) -> bool:
    """Reset TOTP: totp_secret = NULL."""
    row = _db_users.get_by_id(user_id)
    if not row:
        return False
    _db_users.update(user_id, {"totp_secret": None})
    return True



def reset_password(user_id: str, new_password: str) -> bool:
    """Force-reset the password."""
    row = _db_users.get_by_id(user_id)
    if not row:
        return False
    _db_users.update(
        user_id,
        {
            "password": hash_password(new_password),
            "first_login_required": 1,
        },
    )
    return True



def unlock_user(user_id: str) -> bool:
    """Unlock the account: totp_locked_until=NULL, totp_failed_count=0."""
    row = _db_users.get_by_id(user_id)
    if not row:
        return False
    _db_users.update(user_id, {"totp_locked_until": None, "totp_failed_count": 0})
    return True



def get_backup_code_status(user_id: str) -> dict:
    """Backup-code usage status for the user."""
    from modules.flow_gate.db import totp_backup_codes as _bc_db

    rows = _bc_db.list_by_user(user_id)
    total = len(rows)
    used = sum(1 for r in rows if r.get("used_at"))
    last_created = max((r.get("created_at", "") for r in rows), default=None)
    return {
        "total": total,
        "used": used,
        "unused": count_unused(user_id) if rows else 0,
        "last_created": last_created,
    }



def regenerate_backup_codes(user_id: str) -> list[str]:
    """Invalidate existing codes + generate 10 new ones. Return plaintext codes (one time only)."""
    codes = generate_codes()
    store_codes(user_id, codes)
    return codes



def get_user_project_roles(user_id: str) -> list[dict]:
    store = get_store()
    return store._fetch_all(
        "SELECT upr.*, r.role_name FROM user_project_roles upr"
        " JOIN roles r ON r.role_id = upr.role_id"
        " WHERE upr.user_id = ?",
        [user_id],
    )



def assign_project_role(
    user_id: str,
    project_id: str,
    role_id: str,
    granted_by: str | None = None,
) -> dict:
    store = get_store()
    now = now_iso()
    store._execute(
        "INSERT INTO user_project_roles (user_id, project_id, role_id, granted_at, granted_by)"
        " VALUES (?, ?, ?, ?, ?)"
        " ON CONFLICT(user_id, project_id) DO UPDATE SET role_id=excluded.role_id,"
        " granted_at=excluded.granted_at, granted_by=excluded.granted_by",
        [user_id, project_id, role_id, now, granted_by],
    )
    return {"user_id": user_id, "project_id": project_id, "role_id": role_id, "granted_at": now}



def revoke_project_role(user_id: str, project_id: str) -> bool:
    store = get_store()
    store._execute(
        "DELETE FROM user_project_roles WHERE user_id = ? AND project_id = ?",
        [user_id, project_id],
    )
    return True



def _sanitize(user: dict | None) -> dict | None:
    if user is None:
        return None
    return {k: v for k, v in user.items() if k not in ("password", "totp_secret")}



def _sanitize_list(users: list[dict]) -> list[dict]:
    return [_sanitize(u) for u in users]
