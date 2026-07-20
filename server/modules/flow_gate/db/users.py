"""User CRUD."""
from __future__ import annotations
from typing import Optional, Any
from .connection import get_store, now_iso


def _invalidate_auth_cache(user_id: str) -> None:
    """Drop the auth middleware's cached copy of this user row (0276 T0009).

    is_active / is_admin gate access, so a change must apply immediately rather
    than at TTL expiry. Imported lazily: `modules.flow_gate.auth.__init__` pulls
    in middleware and auth_api, which import this module back.
    """
    try:
        from modules.flow_gate.auth import auth_cache
    except Exception:
        return
    auth_cache.invalidate_user(user_id)



def get_by_id(user_id: str) -> Optional[dict]:
    return get_store()._fetch_one(
        "SELECT * FROM users WHERE user_id = ?", [user_id]
    )


def get_by_username(username: str) -> Optional[dict]:
    return get_store()._fetch_one(
        "SELECT * FROM users WHERE username = ?", [username]
    )


def get_by_email(email: str) -> Optional[dict]:
    return get_store()._fetch_one(
        "SELECT * FROM users WHERE email = ?", [email]
    )


def list_users(is_active: int | None = None, limit: int = 100, offset: int = 0) -> list[dict]:
    store = get_store()
    if is_active is not None:
        return store._fetch_all(
            "SELECT * FROM users WHERE is_active = ? LIMIT ? OFFSET ?",
            [is_active, limit, offset],
        )
    return store._fetch_all(
        "SELECT * FROM users LIMIT ? OFFSET ?", [limit, offset]
    )


def create(data: dict[str, Any]) -> dict:
    store = get_store()
    now = now_iso()
    row = {
        "user_id": data["user_id"],
        "username": data["username"],
        "email": data["email"],
        "password": data["password"],
        "totp_secret": data.get("totp_secret"),
        "is_active": data.get("is_active", 1),
        "is_admin": data.get("is_admin", 0),
        "first_login_required": data.get("first_login_required", 0),
        "created_at": data.get("created_at", now),
        "updated_at": data.get("updated_at", now),
    }
    store._execute(
        "INSERT INTO users (user_id, username, email, password, totp_secret, "
        "is_active, is_admin, first_login_required, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            row["user_id"], row["username"], row["email"], row["password"],
            row["totp_secret"], row["is_active"], row["is_admin"],
            row["first_login_required"], row["created_at"], row["updated_at"],
        ],
    )
    _invalidate_auth_cache(row["user_id"])
    return get_by_id(row["user_id"])  # type: ignore[return-value]


def update(user_id: str, updates: dict[str, Any]) -> Optional[dict]:
    store = get_store()
    updates = {k: v for k, v in updates.items() if k not in ("user_id", "created_at")}
    updates["updated_at"] = now_iso()
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    store._execute(
        f"UPDATE users SET {set_clause} WHERE user_id = ?",
        [*updates.values(), user_id],
    )
    # 0276: the auth path caches this row for a few seconds; a change to
    # is_active / is_admin must not wait for the TTL.
    _invalidate_auth_cache(user_id)
    return get_by_id(user_id)


def delete(user_id: str) -> None:
    get_store()._execute("DELETE FROM users WHERE user_id = ?", [user_id])
    _invalidate_auth_cache(user_id)
