"""Per-chat settings of one user: the chat's AI and its context checkpoint (flowgate.v02test.0025).

* **The chat's AI.**  Each chat keeps the AI its user picked, so switching chats no longer carries
  one chat's pick into another. The screen sends the pick with every AI call (``provider_id`` of
  ``/ai-invoke/start``), so what the picker shows is what runs. A stored pick that is no longer
  selectable is not an error on read: the screen falls back to the user's default AI.
* **The context checkpoint.**  A message seq the AI context starts from. Both context paths (the
  API model window in ``ai_invoke.worker`` and the CLI/Agent mention in ``invoke_mention_service``)
  read it through :func:`checkpoint_seq_safe` and pass it to
  ``chat_settings_service.resolve_context_window``, so the two can never disagree. Earlier turns are
  folded, not deleted: the fold notice still lets the AI page back when it really needs them.

Like the user-level chat settings, a broken row must never stop a chat: the context readers never
raise, and a bad value arriving in a PATCH is a 422 naming its field.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from modules.flow_gate.db import conversation_turns
from modules.flow_gate.db import user_conversation_settings as store

_log = logging.getLogger(__name__)

PATCH_FIELDS = ("provider_id", "context_checkpoint_seq")


class ChatDocSettingsError(ValueError):
    """A PATCH field the server refuses (422). ``field`` names the box that is wrong."""

    def __init__(self, field: str, message: str, reason: str = "invalid"):
        super().__init__(message)
        self.field = field
        self.message = message
        self.reason = reason


def _view(row: Optional[dict]) -> dict:
    row = row or {}
    return {
        "provider_id": row.get("provider_id"),
        "context_checkpoint_seq": row.get("context_checkpoint_seq"),
        "updated_at": row.get("updated_at"),
    }


def get_settings(user_id: str, doc_id: str) -> dict:
    """The user's settings of this chat; an unsaved chat answers nulls."""
    return _view(store.get(user_id, doc_id))


def _validate_provider(value: Any, project_id: Optional[str], user_id: str) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ChatDocSettingsError("provider_id", "provider_id must be a string or null.")
    value = value.strip()
    if not value:
        return None
    from modules.flow_gate.settings import ai_settings_service

    try:
        selectable = ai_settings_service.selectable_provider(project_id, value, user_id)
    except LookupError:
        selectable = None
    if selectable is None:
        raise ChatDocSettingsError(
            "provider_id", "provider_id is not an AI you can select in this project.", "provider_unavailable",
        )
    return value


def _validate_checkpoint(value: Any, doc_id: str) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ChatDocSettingsError("context_checkpoint_seq", "context_checkpoint_seq must be an integer or null.")
    head = conversation_turns.current_head_seq(doc_id)
    if value < 1 or value > head:
        raise ChatDocSettingsError(
            "context_checkpoint_seq",
            f"context_checkpoint_seq must be between 1 and {head}.",
            "out_of_range",
        )
    return value


def save_settings(user_id: str, doc_id: str, patch: dict, *, project_id: Optional[str]) -> dict:
    """Validate and store the sent fields only, then answer a fresh read."""
    if not isinstance(patch, dict):
        raise ChatDocSettingsError("body", "The body must be an object.")
    for key in patch:
        if key not in PATCH_FIELDS:
            raise ChatDocSettingsError(key, f"unknown field: {key}.", "unknown_field")
    values: dict = {}
    if "provider_id" in patch:
        values["provider_id"] = _validate_provider(patch["provider_id"], project_id, user_id)
    if "context_checkpoint_seq" in patch:
        values["context_checkpoint_seq"] = _validate_checkpoint(patch["context_checkpoint_seq"], doc_id)
    store.upsert(user_id, doc_id, values)
    return get_settings(user_id, doc_id)


def checkpoint_seq_safe(user_id: Optional[str], doc_id: Optional[str]) -> Optional[int]:
    """The chat's checkpoint for the context paths. Never raises: no checkpoint on any failure."""
    if not user_id or not doc_id:
        return None
    try:
        row = store.get(user_id, doc_id)
    except Exception:  # noqa: BLE001 -- a setting must never stop a chat
        _log.warning("chat checkpoint read failed doc=%s user=%s", doc_id, user_id, exc_info=True)
        return None
    seq = (row or {}).get("context_checkpoint_seq")
    return seq if isinstance(seq, int) and seq >= 1 else None
