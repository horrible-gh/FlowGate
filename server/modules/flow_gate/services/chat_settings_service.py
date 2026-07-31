"""Per-user chat settings and the conversation context window (group 0362, L0010).

Three things live here and nowhere else:

* **The numbers.**  L0010 §1 is the single source of truth for the minimum, maximum,
  default and preset turn counts, and for the two enum domains.  No other module —
  and no screen, and no schema (DB0011 §0) — restates them; the maximum is *derived*
  from the read-side page cap rather than typed a second time, so the day that cap
  moves this one moves with it.
* **Reading and saving.**  ``resolve_chat_settings`` is deliberately the only reader,
  shared by the settings endpoint and the two mention paths (L0010 §2-1).  Two readers
  with two different repairs would show the user one value and apply another.
* **The window.**  ``resolve_context_window`` turns (last read, head, mode, N) into the
  ``after_seq`` a mention advertises and the number of turns that got folded away.

Note the asymmetry between the two directions, which is intended (P0009 시나리오 8):
an unknown value *arriving* in a PATCH is rejected with 422, but an unknown value
already *sitting* in storage is quietly replaced by its default on the way out.  A
setting must never be able to stop someone from talking to an AI.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from modules.flow_gate.db import user_chat_settings as chat_settings_store
from modules.flow_gate.db.connection import now_iso
from modules.flow_gate.services.conversation_query_service import TURN_LIMIT_MAX

_log = logging.getLogger(__name__)

# ── L0010 §1-1 수치 파라미터 ──────────────────────────────────────────────────
CONTEXT_TURNS_MIN = 1
# L0010 §1-4: derived, not copied.  A range the user can pick but the server cannot
# hand over in one read would show up as "I narrowed it to 200 turns, so why is it
# still calling twice" — a difference with no explanation available to them.
CONTEXT_TURNS_MAX = TURN_LIMIT_MAX
CONTEXT_TURNS_DEFAULT = 20
# Drawn in the list; never used to decide anything (L0010 §1-1).
CONTEXT_TURNS_PRESETS = (5, 10, 15, 20, 30)

# ── L0010 §1-2 열거 파라미터 ──────────────────────────────────────────────────
# Byte-for-byte the strings the browser already keeps in localStorage.  Renaming them
# on the way in would buy one translation table, and that table is the accident.
SEND_ACTION_DOMAIN = ("copy_mention", "invoke_ai", "none")
SEND_ACTION_DEFAULT = "none"
CONTEXT_MODE_DOMAIN = ("recent", "all")
CONTEXT_MODE_DEFAULT = "recent"

# Validation order is fixed so that a request with several bad fields always reports
# the same one (L0010 §2-2).
PATCH_FIELDS = ("send_action", "context_mode", "context_turns")


class ChatSettingsError(ValueError):
    """A PATCH field the server refuses. Carries the field name so the screen can
    put the message next to the box that is wrong (P0009 시나리오 7)."""

    def __init__(self, field: str, message: str):
        super().__init__(message)
        self.field = field
        self.message = message


def defaults() -> dict:
    """The settings of somebody who has never saved (L0010 §2-1).

    ``updated_at`` is None here and NOT NULL in the table on purpose: the null is the
    shape of "there is no row", not the value of a column (DB0011 §2-6).
    """
    return {
        "send_action": SEND_ACTION_DEFAULT,
        "context_mode": CONTEXT_MODE_DEFAULT,
        "context_turns": CONTEXT_TURNS_DEFAULT,
        "updated_at": None,
    }


def domain() -> dict:
    """The limits the response carries so no screen has to hold its own copy (P0009 §0-3)."""
    return {
        "send_action": list(SEND_ACTION_DOMAIN),
        "context_mode": list(CONTEXT_MODE_DOMAIN),
        "context_turns_presets": list(CONTEXT_TURNS_PRESETS),
        "context_turns_min": CONTEXT_TURNS_MIN,
        "context_turns_max": CONTEXT_TURNS_MAX,
    }


def _normalize_enum(value: Any, allowed: tuple[str, ...], fallback: str) -> str:
    if value is None:
        return fallback
    return value if value in allowed else fallback


def _normalize_turns(value: Any) -> int:
    """Repair a stored turn count on the way out (L0010 §2-1, §4-3).

    Above the cap the value is pulled *down to the cap*; below the floor it goes back
    to the default.  The two are not symmetric because their causes are not: a value
    over the cap is what a lowered cap leaves behind, and "as many as possible" is
    still the closest reading of that person's intent.  A value under the floor (0,
    negative, or a string that SQLite's affinity rules let through the CHECK — DB0011
    §2-7) is not a shrunken domain, it is a wrong value, and pulling it up to 1 would
    silently freeze them at "the last single turn", which nobody chose.
    """
    if value is None or isinstance(value, bool) or not isinstance(value, int):
        return CONTEXT_TURNS_DEFAULT
    if value < CONTEXT_TURNS_MIN:
        return CONTEXT_TURNS_DEFAULT
    if value > CONTEXT_TURNS_MAX:
        return CONTEXT_TURNS_MAX
    return value


def resolve_chat_settings(user_id: Optional[str]) -> tuple[dict, bool]:
    """Return (settings, is_default) for a user (L0010 §2-1).

    Only the odd field is reverted — a broken ``context_mode`` does not drag
    ``send_action`` back to its default with it (P0009 시나리오 17).  And the repair
    stays in memory: if reading wrote its correction back, the value that caused the
    trouble would already be gone by the time anybody looked at the table.
    """
    if not user_id:
        # Nobody to look up. Same treatment as somebody who has never saved, rather
        # than a second set of defaults living somewhere else (L0010 §5).
        return defaults(), True

    row = chat_settings_store.get(user_id)
    if row is None:
        return defaults(), True

    return {
        "send_action": _normalize_enum(
            row.get("send_action"), SEND_ACTION_DOMAIN, SEND_ACTION_DEFAULT
        ),
        "context_mode": _normalize_enum(
            row.get("context_mode"), CONTEXT_MODE_DOMAIN, CONTEXT_MODE_DEFAULT
        ),
        "context_turns": _normalize_turns(row.get("context_turns")),
        "updated_at": row.get("updated_at"),
    }, False


def resolve_chat_settings_safe(user_id: Optional[str]) -> dict:
    """Settings for the mention paths, which must never fail because of a setting.

    A failed lookup here would surface to the user as "the chat stopped working", with
    nothing anywhere pointing at the settings table as the cause (L0010 §2-5, D0008
    §3-4).  One warning line per request — a stack trace per call would bury the very
    line that explains it.
    """
    try:
        settings, _is_default = resolve_chat_settings(user_id)
        return settings
    except Exception:
        _log.warning("chat settings unavailable; falling back to defaults (user_id=%s)", user_id)
        return defaults()


def validate_patch(patch: dict) -> None:
    """Raise ChatSettingsError on the first bad field, in PATCH_FIELDS order (L0010 §2-2).

    Unknown keys are refused rather than ignored.  Silently dropping one is how "I
    saved it and nothing changed" happens, and that has no trace to follow afterwards.
    """
    for key in patch:
        if key not in PATCH_FIELDS:
            raise ChatSettingsError(key, f"unknown field: {key}.")

    for field in PATCH_FIELDS:
        if field not in patch:
            continue
        value = patch[field]
        if value is None:
            raise ChatSettingsError(field, f"{field} must not be null.")
        if field == "send_action" and value not in SEND_ACTION_DOMAIN:
            raise ChatSettingsError(
                field,
                "send_action must be one of " + ", ".join(SEND_ACTION_DOMAIN) + ".",
            )
        if field == "context_mode" and value not in CONTEXT_MODE_DOMAIN:
            raise ChatSettingsError(
                field,
                "context_mode must be one of " + ", ".join(CONTEXT_MODE_DOMAIN) + ".",
            )
        if field == "context_turns":
            if isinstance(value, bool) or not isinstance(value, int):
                raise ChatSettingsError(field, "context_turns must be an integer.")
            if value < CONTEXT_TURNS_MIN or value > CONTEXT_TURNS_MAX:
                # The two numbers come from §1 rather than the sentence, so the message
                # cannot outlive the limits it describes.
                raise ChatSettingsError(
                    field,
                    f"context_turns must be between {CONTEXT_TURNS_MIN} "
                    f"and {CONTEXT_TURNS_MAX}.",
                )


def save_chat_settings(user_id: Optional[str], patch: dict) -> dict:
    """Validate, write the sent columns only, and answer with a fresh read (L0010 §2-2).

    An empty patch writes nothing and creates nothing — an all-defaults row would flip
    ``is_default`` to false and shut the [전송 시] hand-over down for good (DB0011 §3-3).
    The response is re-read rather than echoed back so that a value the server had to
    repair shows up on the screen immediately (P0009 시나리오 5).
    """
    validate_patch(patch)
    if patch and user_id:
        chat_settings_store.upsert(
            user_id,
            columns=patch,
            unset_columns=defaults(),
            updated_at=now_iso(),
        )
    return settings_response(user_id)


def settings_response(user_id: Optional[str]) -> dict:
    """The envelope both /me/chat-settings verbs answer with (P0009 시나리오 1)."""
    settings, is_default = resolve_chat_settings(user_id)
    stored_defaults = defaults()
    stored_defaults.pop("updated_at", None)
    return {
        "ok": True,
        "settings": settings,
        "is_default": is_default,
        "defaults": stored_defaults,
        "domain": domain(),
    }


def resolve_context_window(
    *, last_read: int, head_seq: int, mode: str, turns: int
) -> tuple[int, int]:
    """Return (start, folded) for one mention (L0010 §2-3).

    ``start`` is where the mention tells the worker to read from; ``folded`` is how many
    turns it has *not* read and is being asked to skip.  They differ whenever the worker
    had already read part of the conversation.

    ``max(last_read, ...)`` is what keeps a range from dragging a caught-up worker
    backwards: narrowing the window is meant to shorten the backlog, not to move
    somebody's read position (P0009 시나리오 15).
    """
    if mode == "all":
        # ``head_seq`` is deliberately not queried on this branch (L0010 §2-3), so it
        # cannot be used to clamp here — and must not be, because [전체] has to produce
        # the very mention this feature never touched (P0009 시나리오 13).
        return max(last_read, 0), 0
    start = max(last_read, head_seq - turns)
    # Covers the short conversation (head_seq - turns is negative) and the abnormal
    # cursor that sits past the end of the conversation (L0010 §5).
    start = min(max(start, 0), head_seq)
    folded = max(0, start - last_read)
    return start, folded
