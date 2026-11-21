"""CRUD for the user_designer_layout table (migration 206).

One row per user: the Designer's left panel width, right panel width and bottom panel height.
The row is separate from ``user_ui_settings`` so that saving a size never creates that table's
row (whose existence means "this person chose a finished-card retention").

Same shape as ``user_ui_settings``: reading never creates a row, ``upsert`` is the single write,
and its SET list holds only the columns the request sent.
"""
from __future__ import annotations

from typing import Optional

from .connection import get_store

SIZE_COLUMNS = ("left_width", "right_width", "bottom_height")

_SELECT = (
    "SELECT user_id, left_width, right_width, bottom_height, created_at, updated_at "
    "FROM user_designer_layout WHERE user_id = ?"
)


def get(user_id: str) -> Optional[dict]:
    """Return this user's stored sizes, or None when they have never saved one."""
    return get_store()._fetch_one(_SELECT, [user_id])


def upsert(
    user_id: str,
    *,
    columns: dict,
    unset_columns: dict,
    updated_at: str,
) -> None:
    """Write only the sizes in ``columns``; a new row takes the rest from ``unset_columns``."""
    if not columns:
        raise ValueError("upsert requires at least one column")

    values = [
        columns[name] if name in columns else unset_columns[name]
        for name in SIZE_COLUMNS
    ]
    set_parts = [f"{name} = excluded.{name}" for name in SIZE_COLUMNS if name in columns]
    set_parts.append("updated_at = excluded.updated_at")
    placeholders = ", ".join(["?"] * (len(SIZE_COLUMNS) + 3))
    get_store()._execute(
        "INSERT INTO user_designer_layout "
        "(user_id, " + ", ".join(SIZE_COLUMNS) + ", created_at, updated_at) "
        f"VALUES ({placeholders}) "
        "ON CONFLICT (user_id) DO UPDATE SET " + ", ".join(set_parts),
        [user_id, *values, updated_at, updated_at],
    )
