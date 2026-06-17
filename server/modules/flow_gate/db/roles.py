"""Role CRUD."""
from __future__ import annotations
from typing import Optional, Any
from .connection import get_store, now_iso


def get_by_id(role_id: str) -> Optional[dict]:
    return get_store()._fetch_one(
        "SELECT * FROM roles WHERE role_id = ?", [role_id]
    )


def list_roles() -> list[dict]:
    return get_store()._fetch_all("SELECT * FROM roles ORDER BY role_id")


def create(data: dict[str, Any]) -> dict:
    store = get_store()
    now = now_iso()
    store._execute(
        "INSERT INTO roles (role_id, role_name, description, is_system, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [
            data["role_id"], data["role_name"], data.get("description"),
            data.get("is_system", 0), data.get("created_at", now), data.get("updated_at", now),
        ],
    )
    return get_by_id(data["role_id"])  # type: ignore[return-value]


def update(role_id: str, updates: dict[str, Any]) -> Optional[dict]:
    store = get_store()
    updates = {k: v for k, v in updates.items() if k not in ("role_id", "created_at")}
    updates["updated_at"] = now_iso()
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    store._execute(
        f"UPDATE roles SET {set_clause} WHERE role_id = ?",
        [*updates.values(), role_id],
    )
    return get_by_id(role_id)


def delete(role_id: str) -> None:
    store = get_store()
    row = get_by_id(role_id)
    if row and row.get("is_system"):
        raise ValueError(f"System role {role_id} cannot be deleted.")
    store._execute("DELETE FROM roles WHERE role_id = ?", [role_id])
