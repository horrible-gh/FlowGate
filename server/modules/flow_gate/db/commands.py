"""Command CRUD."""
from __future__ import annotations

import uuid
from typing import Optional, Any

from .connection import get_store, now_iso


def _new_id() -> str:
    return "cmd-" + uuid.uuid4().hex[:12]


def get_by_id(command_id: str) -> Optional[dict]:
    return get_store()._fetch_one(
        "SELECT * FROM commands WHERE command_id = ?", [command_id]
    )


def list_commands(include_system: bool = False) -> list[dict]:
    store = get_store()
    if include_system:
        return store._fetch_all(
            "SELECT * FROM commands ORDER BY kind, name"
        )
    return store._fetch_all(
        "SELECT * FROM commands WHERE kind = 'user' ORDER BY name"
    )


def create(data: dict[str, Any]) -> dict:
    store = get_store()
    now = now_iso()
    command_id = data.get("command_id") or _new_id()
    store._execute(
        "INSERT INTO commands (command_id, kind, name, template, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [
            command_id,
            data["kind"],
            data["name"],
            data["template"],
            data.get("created_at", now),
            data.get("updated_at", now),
        ],
    )
    return get_by_id(command_id)  # type: ignore[return-value]


def update(command_id: str, updates: dict[str, Any]) -> Optional[dict]:
    store = get_store()
    updates = {k: v for k, v in updates.items() if k not in ("command_id", "kind", "created_at")}
    if not updates:
        return get_by_id(command_id)
    updates["updated_at"] = now_iso()
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    store._execute(
        f"UPDATE commands SET {set_clause} WHERE command_id = ?",
        [*updates.values(), command_id],
    )
    return get_by_id(command_id)


def delete(command_id: str) -> None:
    get_store()._execute("DELETE FROM commands WHERE command_id = ?", [command_id])
