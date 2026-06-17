"""System settings CRUD."""
from __future__ import annotations
from typing import Optional, Any
from .connection import get_store, now_iso


def get(setting_key: str) -> Optional[dict]:
    return get_store()._fetch_one(
        "SELECT * FROM system_settings WHERE setting_key = ?", [setting_key]
    )


def get_value(setting_key: str, default: str | None = None) -> str | None:
    row = get(setting_key)
    if row is None:
        return default
    return row.get("setting_value", default)


def list_settings() -> list[dict]:
    return get_store()._fetch_all(
        "SELECT * FROM system_settings ORDER BY setting_key"
    )


def set_value(
    setting_key: str,
    setting_value: str,
    value_type: str = "string",
    description: str | None = None,
    updated_by: str | None = None,
) -> dict:
    store = get_store()
    now = now_iso()
    store._execute(
        "INSERT INTO system_settings "
        "(setting_key, setting_value, value_type, description, updated_at, updated_by) "
        "VALUES (?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(setting_key) DO UPDATE SET "
        "setting_value=excluded.setting_value, value_type=excluded.value_type, "
        "description=COALESCE(excluded.description, description), "
        "updated_at=excluded.updated_at, updated_by=excluded.updated_by",
        [setting_key, setting_value, value_type, description, now, updated_by],
    )
    return get(setting_key)  # type: ignore[return-value]


def delete(setting_key: str) -> None:
    get_store()._execute(
        "DELETE FROM system_settings WHERE setting_key = ?", [setting_key]
    )


def create(data: dict[str, Any]) -> dict:
    return set_value(
        data["setting_key"],
        data.get("setting_value", ""),
        data.get("value_type", "string"),
        data.get("description"),
        data.get("updated_by"),
    )


def update(setting_key: str, updates: dict[str, Any]) -> Optional[dict]:
    row = get(setting_key)
    if row is None:
        return None
    merged = {**row, **updates}
    return set_value(
        setting_key,
        merged.get("setting_value", ""),
        merged.get("value_type", "string"),
        merged.get("description"),
        merged.get("updated_by"),
    )
