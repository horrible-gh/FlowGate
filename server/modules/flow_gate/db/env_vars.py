"""Environment variable CRUD."""
from __future__ import annotations

import uuid
from typing import Optional, Any

from .connection import get_store, now_iso


def _new_id() -> str:
    return "ev-" + uuid.uuid4().hex[:12]


def get_by_id(var_id: str) -> Optional[dict]:
    return get_store()._fetch_one(
        "SELECT * FROM env_variables WHERE var_id = ?", [var_id]
    )


def list_env_vars(include_system: bool = False) -> list[dict]:
    store = get_store()
    if include_system:
        return store._fetch_all(
            "SELECT * FROM env_variables ORDER BY kind, name"
        )
    return store._fetch_all(
        "SELECT * FROM env_variables WHERE kind = 'user' ORDER BY name"
    )


def create(data: dict[str, Any]) -> dict:
    store = get_store()
    now = now_iso()
    var_id = data.get("var_id") or _new_id()
    store._execute(
        "INSERT INTO env_variables (var_id, kind, name, value, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [
            var_id,
            data["kind"],
            data["name"],
            data.get("value"),
            data.get("created_at", now),
            data.get("updated_at", now),
        ],
    )
    return get_by_id(var_id)  # type: ignore[return-value]


def update(var_id: str, updates: dict[str, Any]) -> Optional[dict]:
    store = get_store()
    updates = {k: v for k, v in updates.items() if k not in ("var_id", "kind", "created_at")}
    if not updates:
        return get_by_id(var_id)
    updates["updated_at"] = now_iso()
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    store._execute(
        f"UPDATE env_variables SET {set_clause} WHERE var_id = ?",
        [*updates.values(), var_id],
    )
    return get_by_id(var_id)


def delete(var_id: str) -> None:
    get_store()._execute("DELETE FROM env_variables WHERE var_id = ?", [var_id])


def get_all_as_map() -> dict[str, str]:
    """Return all environment variables as a {name: value} map (applying user > system precedence)."""
    store = get_store()
    rows = store._fetch_all(
        "SELECT kind, name, value FROM env_variables"
    )
    result: dict[str, str] = {}
    # Fill system first, then let user values override
    for kind in ("system", "user"):
        for row in rows:
            if row["kind"] == kind and row["value"] is not None:
                result[row["name"]] = row["value"]
    return result
