"""Per-user taskbar style. Saving never touches dock sizes or card retention."""
from __future__ import annotations
from typing import Optional
from .connection import get_store

def get(user_id: str) -> Optional[dict]:
    return get_store()._fetch_one(
        "SELECT taskbar_style FROM user_designer_theme WHERE user_id = ?", [user_id]
    )

def save(user_id: str, style: str, updated_at: str) -> None:
    get_store()._execute(
        "INSERT INTO user_designer_theme (user_id, taskbar_style, created_at, updated_at) "
        "VALUES (?, ?, ?, ?) ON CONFLICT (user_id) DO UPDATE SET "
        "taskbar_style = excluded.taskbar_style, updated_at = excluded.updated_at",
        [user_id, style, updated_at, updated_at],
    )
