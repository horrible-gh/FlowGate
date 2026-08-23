"""CRUD for the user_ui_settings table (group 0452, DB0004).

One row per user, holding the non-chat UI preferences.  Today that is a single column,
``ai_finished_card_retention_minutes`` — how long a finished AI-run card survives in the
header monitor (L0003 §1-2).

The absence of a row is itself the answer to "has this person ever chosen a retention"
(DB0004 §2-6), so nothing here creates one implicitly: read paths call :func:`get` only,
:func:`upsert` is the single write and its only caller is the PATCH handler (DB0004 §5-2).

The SET list is built from the columns the request actually sent even though there is
only one of them today.  DB0004 §4-2 asks for that shape now so the day a second UI
preference lands, saving one of them cannot silently rewrite the other.
"""
from __future__ import annotations

from typing import Optional

from .connection import get_store

# The setting columns, in the order DB0004 §4-2's INSERT lists them.  A tuple so the SET
# list below is built from the same source as the VALUES list.
SETTING_COLUMNS = ("ai_finished_card_retention_minutes",)

_SELECT = (
    "SELECT user_id, ai_finished_card_retention_minutes, created_at, updated_at "
    "FROM user_ui_settings WHERE user_id = ?"
)


def get(user_id: str) -> Optional[dict]:
    """Return this user's stored row, or None when they have never saved (DB0004 §4-1)."""
    return get_store()._fetch_one(_SELECT, [user_id])


def upsert(
    user_id: str,
    *,
    columns: dict,
    unset_columns: dict,
    updated_at: str,
) -> None:
    """Write only the columns in ``columns``; fill the rest on INSERT from ``unset_columns``.

    The UPDATE branch touches the sent columns plus ``updated_at`` and nothing else —
    notably not ``created_at``, which would otherwise be reset on every save and stop
    meaning "when this row first appeared" (DB0004 §4-2).
    """
    if not columns:
        # An empty patch must not reach storage at all: a row written here would flip
        # "this user has never saved" to false for somebody who chose nothing
        # (DB0004 §2-6, §4-2, L0003 §2-8).
        raise ValueError("upsert requires at least one column")

    values = [
        columns[name] if name in columns else unset_columns[name]
        for name in SETTING_COLUMNS
    ]
    set_parts = [f"{name} = excluded.{name}" for name in SETTING_COLUMNS if name in columns]
    set_parts.append("updated_at = excluded.updated_at")
    placeholders = ", ".join(["?"] * (len(SETTING_COLUMNS) + 3))
    get_store()._execute(
        "INSERT INTO user_ui_settings "
        "(user_id, " + ", ".join(SETTING_COLUMNS) + ", created_at, updated_at) "
        f"VALUES ({placeholders}) "
        "ON CONFLICT (user_id) DO UPDATE SET " + ", ".join(set_parts),
        [user_id, *values, updated_at, updated_at],
    )
