"""TOTP backup code CRUD.

Managed separately by the auth2fa library, but this is an MVP wrapper for cases that require direct access.
The actual table name follows the name created by the auth2fa library.
This module assumes the backup_codes table exists (created by auth2fa).
"""
from __future__ import annotations
from typing import Optional
from .connection import get_store, now_iso

_TABLE = "backup_codes"


def list_by_user(user_id: str) -> list[dict]:
    try:
        return get_store()._fetch_all(
            f"SELECT * FROM {_TABLE} WHERE user_id = ?", [user_id]
        )
    except Exception:
        return []


def get_by_code(user_id: str, code: str) -> Optional[dict]:
    try:
        return get_store()._fetch_one(
            f"SELECT * FROM {_TABLE} WHERE user_id = ? AND code = ?", [user_id, code]
        )
    except Exception:
        return None


def mark_used(user_id: str, code: str) -> None:
    try:
        now = now_iso()
        get_store()._execute(
            f"UPDATE {_TABLE} SET used_at = ? "
            "WHERE user_id = ? AND code = ? AND used_at IS NULL",
            [now, user_id, code],
        )
    except Exception:
        pass


def delete_all(user_id: str) -> None:
    try:
        get_store()._execute(
            f"DELETE FROM {_TABLE} WHERE user_id = ?", [user_id]
        )
    except Exception:
        pass


def create(data: dict) -> Optional[dict]:
    try:
        get_store()._execute(
            f"INSERT INTO {_TABLE} (user_id, code) VALUES (?, ?)",
            [data["user_id"], data["code"]],
        )
        return get_by_code(data["user_id"], data["code"])
    except Exception:
        return None


def list(user_id: str) -> list[dict]:
    return list_by_user(user_id)


def delete(user_id: str, code: str) -> None:
    try:
        get_store()._execute(
            f"DELETE FROM {_TABLE} WHERE user_id = ? AND code = ?", [user_id, code]
        )
    except Exception:
        pass
