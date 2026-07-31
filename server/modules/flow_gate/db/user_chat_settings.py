"""CRUD for the user_chat_settings table (group 0362, DB0011).

One row per user, holding the three chat settings P0009 §0-2 named: ``send_action``,
``context_mode``, ``context_turns``.  The absence of a row is itself the answer to
"has this person ever chosen anything" — DB0011 §2-6 — so nothing here creates a row
implicitly.  Read paths call :func:`get` only; :func:`upsert` is the single write and
its only caller is the PATCH handler (DB0011 §5-2).

Column-at-a-time updating is the one thing L0010 §2-2 asks of storage: a request that
carries only ``context_mode`` must leave the stored ``context_turns`` alone, so the
number the user was using is still there when they switch back to [최근].
"""
from __future__ import annotations

from typing import Optional

from .connection import get_store

# The three setting columns, in the order DB0011 §4-2's INSERT lists them.  Kept as a
# tuple so the SET list below is built from the same source as the VALUES list.
SETTING_COLUMNS = ("send_action", "context_mode", "context_turns")

_SELECT = (
    "SELECT user_id, send_action, context_mode, context_turns, created_at, updated_at "
    "FROM user_chat_settings WHERE user_id = ?"
)


def get(user_id: str) -> Optional[dict]:
    """Return this user's stored row, or None when they have never saved (DB0011 §4-1)."""
    return get_store()._fetch_one(_SELECT, [user_id])


def upsert(
    user_id: str,
    *,
    columns: dict,
    unset_columns: dict,
    updated_at: str,
) -> None:
    """Write only the columns in ``columns``; fill the rest on INSERT from ``unset_columns``.

    ``columns`` carries the fields the request actually sent, ``unset_columns`` the
    defaults used to satisfy NOT NULL when the row does not exist yet.  The UPDATE
    branch touches the sent columns plus ``updated_at`` and nothing else — notably not
    ``created_at``, which would otherwise be reset on every save and stop meaning
    "when this row first appeared" (DB0011 §4-2).
    """
    if not columns:
        # An empty patch must not reach storage at all: writing here would create a
        # default row and flip is_default to false, which permanently blocks the
        # [전송 시] hand-over (L0010 §2-2, DB0011 §3-3).
        raise ValueError("upsert requires at least one column")

    values = [
        columns[name] if name in columns else unset_columns[name]
        for name in SETTING_COLUMNS
    ]
    set_parts = [f"{name} = excluded.{name}" for name in SETTING_COLUMNS if name in columns]
    set_parts.append("updated_at = excluded.updated_at")
    get_store()._execute(
        "INSERT INTO user_chat_settings "
        "(user_id, send_action, context_mode, context_turns, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?) "
        "ON CONFLICT (user_id) DO UPDATE SET " + ", ".join(set_parts),
        [user_id, *values, updated_at, updated_at],
    )
