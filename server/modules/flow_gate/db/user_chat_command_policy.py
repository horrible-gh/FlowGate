"""CRUD for the user_chat_command_policy table (flowgate.default.0670 T0004).

One row per user holding the chat command execution policy. Kept in its own table
for the same reason ``user_chat_source_access`` is: saving the command policy alone
must never create a ``user_chat_settings`` row, whose mere existence is what
``is_default`` (and the one-time [on send] hand-over) reads. The absence of a row
means "never chosen" and resolves to the service default (``user_approval``).
"""
from __future__ import annotations

from typing import Optional

from .connection import get_store


def get(user_id: str) -> Optional[dict]:
    return get_store()._fetch_one(
        "SELECT user_id, command_policy, created_at, updated_at "
        "FROM user_chat_command_policy WHERE user_id = ?",
        [user_id],
    )


def upsert(user_id: str, command_policy: str, updated_at: str) -> None:
    get_store()._execute(
        "INSERT INTO user_chat_command_policy (user_id, command_policy, created_at, updated_at) "
        "VALUES (?, ?, ?, ?) "
        "ON CONFLICT (user_id) DO UPDATE SET command_policy = excluded.command_policy, "
        "updated_at = excluded.updated_at",
        [user_id, command_policy, updated_at, updated_at],
    )
