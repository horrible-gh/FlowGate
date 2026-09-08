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

Note the asymmetry between the two directions, which is intended (P0009 scenario 8):
an unknown value *arriving* in a PATCH is rejected with 422, but an unknown value
already *sitting* in storage is quietly replaced by its default on the way out.  A
setting must never be able to stop someone from talking to an AI.

0515 T0009 (D0006, L0007, DB0008) adds a second, independent per-user setting --
``source_access_mode`` -- living in its own ``user_chat_source_access`` storage
(D0006 §2.1: kept separate so this table's own ``is_default``/legacy-migration
meaning is never touched by it). :func:`resolve_chat_settings` merges the two
storages on read; :func:`save_chat_settings` writes each patched half to its own
storage inside one transaction when both arrive together (L0007 §2.2.2). The
``edit_once`` one-shot claim state machine (claim/commit/rollback) itself lives in
``db.user_chat_source_access`` and ``token_service``; this module only owns the
stale/corrupted-marker recovery sweep (:func:`recover_stale_claim`, L0007 §2.4.4)
and the settings-read/PATCH contract around it.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from modules.flow_gate.db import user_chat_settings as chat_settings_store
from modules.flow_gate.db import user_chat_source_access as source_access_store
from modules.flow_gate.db.connection import get_store, now_iso
from modules.flow_gate.services.conversation_query_service import TURN_LIMIT_MAX

_log = logging.getLogger(__name__)

# -- L0010 §1-1 numeric parameters -------------------------------------------
CONTEXT_TURNS_MIN = 1
# L0010 §1-4: derived, not copied.  A range the user can pick but the server cannot
# hand over in one read would show up as "I narrowed it to 200 turns, so why is it
# still calling twice" -- a difference with no explanation available to them.
CONTEXT_TURNS_MAX = TURN_LIMIT_MAX
CONTEXT_TURNS_DEFAULT = 20
# Drawn in the list; never used to decide anything (L0010 §1-1).
CONTEXT_TURNS_PRESETS = (5, 10, 15, 20, 30)

# -- L0010 §1-2 enumerated parameters ----------------------------------------
# Byte-for-byte the strings the browser already keeps in localStorage.  Renaming them
# on the way in would buy one translation table, and that table is the accident.
SEND_ACTION_DOMAIN = ("copy_mention", "invoke_ai", "none")
SEND_ACTION_DEFAULT = "none"
CONTEXT_MODE_DOMAIN = ("recent", "all")
CONTEXT_MODE_DEFAULT = "recent"

# -- 0515 D0006 §2.2 domain -- kept in its own separate storage (§3.1) ------
SOURCE_ACCESS_DOMAIN = ("read_only", "edit", "edit_once")
SOURCE_ACCESS_DEFAULT = "read_only"

# 0515 T0009 §5: a claim marker older than this is eligible for stale recovery.
ONE_SHOT_CLAIM_STALE_SEC = 600

# The three columns user_chat_settings.upsert() owns (T0009 §3.1 -- restated, not
# imported, from db.user_chat_settings.SETTING_COLUMNS: save_chat_settings must keep
# working against a test double for that store that implements .get()/.upsert() only).
_LEGACY_SETTING_FIELDS = ("send_action", "context_mode", "context_turns")

# Validation order is fixed so that a request with several bad fields always reports
# the same one (L0010 §2-2). source_access_mode is appended last (T0009 §3.3) -- the
# three legacy fields keep their original relative order and error precedence.
PATCH_FIELDS = (*_LEGACY_SETTING_FIELDS, "source_access_mode")


class ChatSettingsError(ValueError):
    """A PATCH field the server refuses. Carries the field name so the screen can
    put the message next to the box that is wrong (P0009 scenario 7)."""

    def __init__(self, field: str, message: str):
        super().__init__(message)
        self.field = field
        self.message = message


def defaults() -> dict:
    """The settings of somebody who has never saved (L0010 §2-1).

    ``updated_at`` is None here and NOT NULL in the table on purpose: the null is the
    shape of "there is no row", not the value of a column (DB0011 §2-6). Unchanged by
    0515 T0009 (§3.1) -- ``source_access_mode`` lives in its own storage and is merged
    in separately by :func:`resolve_chat_settings`, never inside this dict's own keys.
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
        "source_access_mode": list(SOURCE_ACCESS_DOMAIN),
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
    negative, or a string that SQLite's affinity rules let through the CHECK -- DB0011
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

    Only the odd field is reverted -- a broken ``context_mode`` does not drag
    ``send_action`` back to its default with it (P0009 scenario 17).  And the repair
    stays in memory: if reading wrote its correction back, the value that caused the
    trouble would already be gone by the time anybody looked at the table.

    0515 T0009 §3.2: ``source_access_mode`` is read from its own storage and merged in
    on every path below, including both "never saved" branches -- its presence must
    never depend on whether a ``user_chat_settings`` row exists, and ``is_default``
    below is computed from that legacy row alone, exactly as before this T.
    """
    source_access_mode = (
        source_access_store.resolve_source_access_mode(user_id) if user_id else SOURCE_ACCESS_DEFAULT
    )

    if not user_id:
        # Nobody to look up. Same treatment as somebody who has never saved, rather
        # than a second set of defaults living somewhere else (L0010 §5).
        settings = defaults()
        settings["source_access_mode"] = source_access_mode
        return settings, True

    row = chat_settings_store.get(user_id)
    if row is None:
        settings = defaults()
        settings["source_access_mode"] = source_access_mode
        return settings, True

    return {
        "send_action": _normalize_enum(
            row.get("send_action"), SEND_ACTION_DOMAIN, SEND_ACTION_DEFAULT
        ),
        "context_mode": _normalize_enum(
            row.get("context_mode"), CONTEXT_MODE_DOMAIN, CONTEXT_MODE_DEFAULT
        ),
        "context_turns": _normalize_turns(row.get("context_turns")),
        "updated_at": row.get("updated_at"),
        "source_access_mode": source_access_mode,
    }, False


def resolve_chat_settings_safe(user_id: Optional[str]) -> dict:
    """Settings for the mention paths, which must never fail because of a setting.

    A failed lookup here would surface to the user as "the chat stopped working", with
    nothing anywhere pointing at the settings table as the cause (L0010 §2-5, D0008
    §3-4).  One warning line per request -- a stack trace per call would bury the very
    line that explains it.
    """
    try:
        settings, _is_default = resolve_chat_settings(user_id)
        return settings
    except Exception:
        _log.warning("chat settings unavailable; falling back to defaults (user_id=%s)", user_id)
        fallback = defaults()
        fallback["source_access_mode"] = SOURCE_ACCESS_DEFAULT
        return fallback


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
        if field == "source_access_mode" and value not in SOURCE_ACCESS_DOMAIN:
            raise ChatSettingsError(
                field,
                "source_access_mode must be one of " + ", ".join(SOURCE_ACCESS_DOMAIN) + ".",
            )


def save_chat_settings(user_id: Optional[str], patch: dict) -> dict:
    """Validate, write the sent columns only, and answer with a fresh read (L0010 §2-2).

    An empty patch writes nothing and creates nothing -- an all-defaults row would flip
    ``is_default`` to false and shut the [on send] hand-over down for good (DB0011 §3-3).
    The response is re-read rather than echoed back so that a value the server had to
    repair shows up on the screen immediately (P0009 scenario 5).

    0515 T0009 §3.3: ``source_access_mode`` is written to its own storage. When a PATCH
    carries both a legacy field and ``source_access_mode``, both writes happen inside one
    ``store.transaction()`` (T0009 §3.3) so they land or fail together. A source-only
    PATCH never touches (and never creates) the ``user_chat_settings`` row (T0009 §3.3,
    §12.2) -- ``source_access_store.upsert`` is called on its own, outside any legacy
    upsert call.
    """
    validate_patch(patch)
    if patch and user_id:
        # Hardcoded rather than read off chat_settings_store.SETTING_COLUMNS: this
        # function must keep working against a test double that stands in for that
        # store's .get()/.upsert() surface only (see test_chat_settings_0362.py's
        # _MemoryStore), and _LEGACY_SETTING_FIELDS already has to name these three
        # fields itself to split a mixed patch (T0009 §3.3).
        legacy_patch = {key: patch[key] for key in _LEGACY_SETTING_FIELDS if key in patch}
        source_mode = patch.get("source_access_mode")
        if legacy_patch or source_mode is not None:
            now = now_iso()
            with get_store().transaction():
                if legacy_patch:
                    chat_settings_store.upsert(
                        user_id,
                        columns=legacy_patch,
                        unset_columns=defaults(),
                        updated_at=now,
                    )
                if source_mode is not None:
                    source_access_store.upsert(user_id, source_mode, now)
    return settings_response(user_id)


def settings_response(user_id: Optional[str]) -> dict:
    """The envelope both /me/chat-settings verbs answer with (P0009 scenario 1).

    0515 T0009 §5.1: runs the stale/corrupted one-shot claim recovery sweep for this
    user before reading, so a claim nobody will ever commit (a dead worker, a crashed
    tab) does not keep blocking a fresh ``edit_once`` claim forever.
    """
    if user_id:
        recover_stale_claim(user_id)
    settings, is_default = resolve_chat_settings(user_id)
    stored_defaults = defaults()
    stored_defaults.pop("updated_at", None)
    stored_defaults["source_access_mode"] = SOURCE_ACCESS_DEFAULT
    return {
        "ok": True,
        "settings": settings,
        "is_default": is_default,
        "defaults": stored_defaults,
        "domain": domain(),
    }


def _is_stale(claimed_at: Optional[str]) -> bool:
    """True once ``claimed_at`` is at least :data:`ONE_SHOT_CLAIM_STALE_SEC` old."""
    if not claimed_at:
        return False
    try:
        stamp = datetime.fromisoformat(claimed_at)
    except ValueError:
        # An unparsable timestamp cannot be a fresh claim either; treat it as stale
        # rather than let a corrupted value block recovery forever.
        return True
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    age = (datetime.now(timezone.utc) - stamp).total_seconds()
    return age >= ONE_SHOT_CLAIM_STALE_SEC


def recover_stale_claim(user_id: str) -> str:
    """Classify and repair one stale/corrupted ``edit_once`` claim marker (T0009 §5).

    Returns one of ``NOTHING_TO_DO``, ``B_TYPE_CLEANED``, ``MISSING_RECOVERED``,
    ``REVOKED_RECOVERED``, ``LIVE_REVOKED``, ``CONSUMED_MARKER_CLEANED`` -- tests and
    callers that need to see which branch ran can inspect it; nothing here raises for
    an ordinary AVAILABLE row.

    Called from :func:`settings_response` and from ``token_service.issue()``'s
    pre-claim step (T0009 §5.1) -- no other call site should be added; both already
    reach every user who could be looking at a stuck claim.
    """
    row = source_access_store.get(user_id)
    if row is None:
        return "NOTHING_TO_DO"

    token_id = row.get("one_shot_token_id")
    claimed_at = row.get("one_shot_claimed_at")
    if token_id is None:
        if claimed_at is None:
            return "NOTHING_TO_DO"  # normal AVAILABLE row
        # B-type corruption (DB0008 §5.2): no token identity survives, so this can
        # never re-arm edit_once -- fail-closed, marker-only cleanup.
        source_access_store.clear_b_type_marker(user_id, now_iso())
        return "B_TYPE_CLEANED"

    if not _is_stale(claimed_at):
        return "NOTHING_TO_DO"

    # Lazy imports: db.tokens/services.token_service are not part of this module's
    # normal import graph, and token_service imports nothing from here, so this stays
    # a one-directional dependency edge introduced only inside this function.
    from modules.flow_gate.db import tokens as db_tokens

    token_rec = db_tokens.get_by_id(token_id)
    if token_rec is None:
        source_access_store.rollback(token_id, now_iso())
        return "MISSING_RECOVERED"
    if token_rec.get("consumed_at"):
        # edit_once must never be re-armed for a token that was genuinely used
        # (T0009 §5, DB0008 §5.2) -- clear the marker only, and flag the invariant
        # this should not normally reach (a CLAIMED row surviving past commit()).
        source_access_store.commit(user_id, token_id, now_iso())
        _log.warning(
            "edit_once claim invariant violated: token %s already CONSUMED while still "
            "CLAIMED for user %s; marker cleared without re-arming edit_once",
            token_id, user_id,
        )
        return "CONSUMED_MARKER_CLEANED"
    if token_rec.get("revoked_at"):
        source_access_store.rollback(token_id, now_iso())
        return "REVOKED_RECOVERED"

    # LIVE: ask token_service to revoke it. Its own claim-winner -> rollback wiring
    # (T0009 §4.4) performs the marker rollback as soon as *some* caller's revoke
    # actually wins the CAS -- including this one -- so no second rollback call is
    # needed here; re-calling rollback() afterwards would just be a harmless no-op.
    from modules.flow_gate.services import token_service

    token_service.revoke(token_id, reason="chat_one_shot_stale")
    refreshed = db_tokens.get_by_id(token_id)
    if refreshed is not None and refreshed.get("consumed_at"):
        # Lost the revoke race to a legitimate consume landing in the same window.
        source_access_store.commit(user_id, token_id, now_iso())
        _log.warning(
            "edit_once claim recovery raced a legitimate consume: token %s for user %s",
            token_id, user_id,
        )
        return "CONSUMED_MARKER_CLEANED"
    return "LIVE_REVOKED"


def resolve_context_window(
    *, last_read: int, head_seq: int, mode: str, turns: int
) -> tuple[int, int]:
    """Return (start, folded) for one mention (L0010 §2-3).

    ``start`` is where the mention tells the worker to read from; ``folded`` is how many
    turns it has *not* read and is being asked to skip.  They differ whenever the worker
    had already read part of the conversation.

    ``max(last_read, ...)`` is what keeps a range from dragging a caught-up worker
    backwards: narrowing the window is meant to shorten the backlog, not to move
    somebody's read position (P0009 scenario 15).
    """
    if mode == "all":
        # ``head_seq`` is deliberately not queried on this branch (L0010 §2-3), so it
        # cannot be used to clamp here -- and must not be, because [all] has to produce
        # the very mention this feature never touched (P0009 scenario 13).
        return max(last_read, 0), 0
    start = max(last_read, head_seq - turns)
    # Covers the short conversation (head_seq - turns is negative) and the abnormal
    # cursor that sits past the end of the conversation (L0010 §5).
    start = min(max(start, 0), head_seq)
    folded = max(0, start - last_read)
    return start, folded
