"""Per-chat settings of one user: the chat's AI and its context checkpoint (flowgate.v02test.0025).

* **The chat's AI.**  Each chat keeps the AI its user picked, so switching chats no longer carries
  one chat's pick into another. The screen sends the pick with every AI call (``provider_id`` of
  ``/ai-invoke/start``), so what the picker shows is what runs. A stored pick that is no longer
  selectable is not an error on read: the screen falls back to the user's default AI.
* **The context checkpoint.**  A message seq plus a kind: ``include`` (send from this message) or
  ``exclude`` (send from the next message). It applies only while the user's context mode is
  [checkpoint]; [checkpoint] without a checkpoint in this chat sends the last 5 turns, and [recent] /
  [all] ignore it (the stored checkpoint is kept, so going back to [checkpoint] applies it again).
  Both context paths (the API model window in ``ai_invoke.worker`` and the CLI/Agent mention in
  ``invoke_mention_service``) get their window from :func:`context_window`, so the two can never
  disagree. Earlier turns are folded, not deleted: the fold notice still lets the AI page back.

Like the user-level chat settings, a broken row must never stop a chat: the context readers never
raise, and a bad value arriving in a PATCH is a 422 naming its field.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Optional

from modules.flow_gate.db import conversation_turns
from modules.flow_gate.db import user_conversation_settings as store
from modules.flow_gate.services import chat_settings_service

_log = logging.getLogger(__name__)

PATCH_FIELDS = ("provider_id", "context_checkpoint_seq", "context_checkpoint_kind")
CHECKPOINT_KINDS = store.CHECKPOINT_KINDS
CHECKPOINT_KIND_DEFAULT = store.CHECKPOINT_KIND_DEFAULT


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
        "context_checkpoint_kind": row.get("context_checkpoint_kind"),
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


def _validate_kind(value: Any) -> str:
    if value not in CHECKPOINT_KINDS:
        raise ChatDocSettingsError(
            "context_checkpoint_kind", "context_checkpoint_kind must be one of " + ", ".join(CHECKPOINT_KINDS) + ".",
        )
    return value


def save_settings(user_id: str, doc_id: str, patch: dict, *, project_id: Optional[str]) -> dict:
    """Validate and store the sent fields only, then answer a fresh read.

    The checkpoint is one value of two columns: a seq without a kind is ``include``, clearing the seq clears the
    kind, and a kind alone changes the kind of the checkpoint already set (422 when there is none).
    """
    if not isinstance(patch, dict):
        raise ChatDocSettingsError("body", "The body must be an object.")
    for key in patch:
        if key not in PATCH_FIELDS:
            raise ChatDocSettingsError(key, f"unknown field: {key}.", "unknown_field")
    values: dict = {}
    if "provider_id" in patch:
        values["provider_id"] = _validate_provider(patch["provider_id"], project_id, user_id)
    if "context_checkpoint_seq" in patch:
        seq = _validate_checkpoint(patch["context_checkpoint_seq"], doc_id)
        values["context_checkpoint_seq"] = seq
        if seq is None:
            values["context_checkpoint_kind"] = None
        else:
            values["context_checkpoint_kind"] = _validate_kind(patch.get("context_checkpoint_kind", CHECKPOINT_KIND_DEFAULT))
    elif "context_checkpoint_kind" in patch:
        kind = _validate_kind(patch["context_checkpoint_kind"])
        if (store.get(user_id, doc_id) or {}).get("context_checkpoint_seq") is None:
            raise ChatDocSettingsError(
                "context_checkpoint_kind", "There is no checkpoint in this chat to change.", "no_checkpoint",
            )
        values["context_checkpoint_kind"] = kind
    store.upsert(user_id, doc_id, values)
    return get_settings(user_id, doc_id)


def checkpoint_safe(user_id: Optional[str], doc_id: Optional[str]) -> Optional[tuple[int, bool]]:
    """The chat's checkpoint as ``(seq, include)`` for the context paths, or None.

    Never raises: no checkpoint on any failure.
    """
    if not user_id or not doc_id:
        return None
    try:
        row = store.get(user_id, doc_id)
    except Exception:  # noqa: BLE001 -- a setting must never stop a chat
        _log.warning("chat checkpoint read failed doc=%s user=%s", doc_id, user_id, exc_info=True)
        return None
    seq = (row or {}).get("context_checkpoint_seq")
    if not (isinstance(seq, int) and not isinstance(seq, bool) and seq >= 1):
        return None
    return seq, (row or {}).get("context_checkpoint_kind") != "exclude"


def checkpoint_seq_safe(user_id: Optional[str], doc_id: Optional[str]) -> Optional[int]:
    """The chat's checkpoint seq (whatever its kind), or None. Never raises."""
    checkpoint = checkpoint_safe(user_id, doc_id)
    return checkpoint[0] if checkpoint else None


def context_window(
    user_id: Optional[str],
    doc_id: str,
    settings: dict,
    *,
    head_seq_of: Callable[[str], int],
) -> dict:
    """The one window both context paths send: mode, limit, ``after_seq``, ``folded`` and the applied checkpoint.

    ``head_seq_of`` is the caller's own head reader, called only when the window counts back from the head.
    ``checkpoint_seq`` / ``checkpoint_kind`` are set only when a checkpoint actually decided the window.
    """
    mode = settings["context_mode"]
    turns = int(settings["context_turns"])
    checkpoint = checkpoint_safe(user_id, doc_id) if mode == "checkpoint" else None
    seq, include = checkpoint if checkpoint else (None, True)
    head_seq = head_seq_of(doc_id) if chat_settings_service.window_needs_head(mode, seq) else 0
    after_seq, folded = chat_settings_service.resolve_context_window(
        head_seq=head_seq, mode=mode, turns=turns, checkpoint_seq=seq, checkpoint_include=include,
    )
    return {
        "mode": mode,
        "turns": turns,
        "limit": chat_settings_service.context_turn_limit(mode, turns, seq),
        "after_seq": after_seq,
        "folded": folded,
        "checkpoint_seq": seq,
        "checkpoint_kind": None if seq is None else ("include" if include else "exclude"),
    }
