"""Per-user, non-chat UI settings — today just the finished-card retention (0452, L0003).

Three things live here and nowhere else:

* **The numbers.**  L0003 §1-1 is the single source of truth for the nine values a user
  may pick and for the default.  No screen and no schema (DB0004 §0-2) restates them;
  the GET/PATCH envelope ships the domain so the settings screen draws the server's list
  instead of holding a copy that eventually disagrees.
* **Reading.**  ``resolve_ui_settings`` is the only reader.  Two readers with two
  different repairs would show one value on the screen and apply another.
* **Saving.**  ``save_ui_settings`` validates, writes the sent fields only, and answers
  with a *fresh read* rather than an echo, so a value the server had to repair is on the
  screen immediately.

Two traps are worth naming because both are easy to reintroduce:

``-1`` is a full member of the domain ("never expires"), not a lower bound.  The
normalizer is therefore a **membership test**, never a range clamp — a clamp would
quietly turn the choice somebody made into 30 minutes (L0003 §2-1, §5).

And the direction of forgiveness is asymmetric on purpose (L0003 §2-8): a value
*arriving* in a PATCH is refused with a 422, but a value already *sitting* in storage
is quietly replaced by the default on the way out and the call proceeds.  A setting must
never be able to stop finished cards from working.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from modules.flow_gate.db import user_ui_settings as ui_settings_store
from modules.flow_gate.db.connection import now_iso

_log = logging.getLogger(__name__)

# ── L0003 §1-1 numeric parameters ───────────────────────────────────────────
# The closed list, in the order the screen draws it.  -1 = never expires,
# 0 = no finished card at all; the rest are minutes.
RETENTION_DOMAIN_MINUTES = (-1, 0, 30, 60, 120, 180, 360, 720, 1440)
RETENTION_DEFAULT_MINUTES = 30
RETENTION_NEVER = -1
RETENTION_IMMEDIATE = 0

# ── L0003 §1-2 identifiers ──────────────────────────────────────────────────
# The stored column name, the response field name and the client type's key are all this
# one string.  A translation table between layers is the accident, not the safeguard.
RETENTION_FIELD = "ai_finished_card_retention_minutes"

# Validation order is fixed so a request with several bad fields always reports the same
# one.  One field today; the tuple is what keeps that stable when a second arrives.
PATCH_FIELDS = (RETENTION_FIELD,)


class UiSettingsError(ValueError):
    """A PATCH field the server refuses.  Carries the field name so the screen can put
    the message next to the control that is wrong."""

    def __init__(self, field: str, message: str):
        super().__init__(message)
        self.field = field
        self.message = message


def defaults() -> dict:
    """The settings of somebody who has never saved (L0003 §4-4, branch 4).

    ``updated_at`` is None here and NOT NULL in the table on purpose: the null is the
    shape of "there is no row", not the value of a column (DB0004 §2-6).
    """
    return {
        RETENTION_FIELD: RETENTION_DEFAULT_MINUTES,
        "updated_at": None,
    }


def domain() -> dict:
    """The choices the response carries so no screen holds its own copy (L0003 §2-8)."""
    return {RETENTION_FIELD: list(RETENTION_DOMAIN_MINUTES)}


def normalize_retention_minutes(value: Any) -> int:
    """Repair a stored retention on the way out (L0003 §2-1).

    Membership, not range.  ``-1`` is a legitimate choice and ``bool`` is not an integer
    here: Python would happily let ``True`` through ``isinstance(value, int)`` and then
    compare equal to 1, which is not in the domain anyway — but the explicit rejection
    keeps that accident from depending on the domain's contents.
    """
    if value is None or isinstance(value, bool) or not isinstance(value, int):
        return RETENTION_DEFAULT_MINUTES
    if value not in RETENTION_DOMAIN_MINUTES:
        return RETENTION_DEFAULT_MINUTES
    return value


def resolve_ui_settings(user_id: Optional[str]) -> tuple[dict, bool]:
    """Return (settings, is_default) for a user (L0003 §4-4).

    The repair stays in memory: if reading wrote its correction back, the value that
    caused the trouble would already be gone by the time anybody looked at the table
    (DB0004 §2-4).  Reading also never creates a row — that absence is the answer to
    "has this person ever saved" (DB0004 §5-2).
    """
    if not user_id:
        # Nobody to look up.  Same treatment as somebody who has never saved, rather
        # than a second definition of the default living somewhere else (L0003 §5).
        return defaults(), True

    row = ui_settings_store.get(user_id)
    if row is None:
        return defaults(), True

    return {
        RETENTION_FIELD: normalize_retention_minutes(row.get(RETENTION_FIELD)),
        "updated_at": row.get("updated_at"),
    }, False


def resolve_ui_settings_safe(user_id: Optional[str]) -> dict:
    """Settings for callers that must never fail because of a setting (L0003 §5).

    A failed lookup here would surface as "the finished cards stopped working", with
    nothing pointing at this table as the cause.  One warning line per call — a stack
    trace would bury the very line that explains it.
    """
    try:
        settings, _is_default = resolve_ui_settings(user_id)
        return settings
    except Exception:
        _log.warning("ui settings unavailable; falling back to defaults (user_id=%s)", user_id)
        return defaults()


def validate_patch(patch: dict) -> None:
    """Raise UiSettingsError on the first bad field, in PATCH_FIELDS order (L0003 §2-8).

    Unknown keys are refused rather than ignored.  Silently dropping one is how "I saved
    it and nothing changed" happens, and that leaves no trace to follow afterwards.
    """
    for key in patch:
        if key not in PATCH_FIELDS:
            raise UiSettingsError(key, f"unknown field: {key}.")

    for field in PATCH_FIELDS:
        if field not in patch:
            continue
        value = patch[field]
        if value is None:
            raise UiSettingsError(field, f"{field} must not be null.")
        if isinstance(value, bool) or not isinstance(value, int):
            raise UiSettingsError(field, f"{field} must be an integer.")
        if value not in RETENTION_DOMAIN_MINUTES:
            # The list comes from §1-1 rather than the sentence, so the message cannot
            # outlive the domain it describes.
            raise UiSettingsError(
                field,
                f"{field} must be one of "
                + ", ".join(str(v) for v in RETENTION_DOMAIN_MINUTES)
                + ".",
            )


def save_ui_settings(user_id: Optional[str], patch: dict) -> dict:
    """Validate, write the sent columns only, and answer with a fresh read (L0003 §2-8).

    An empty patch writes nothing and creates nothing: a defaults row would flip
    ``is_default`` to false for somebody who never chose anything (DB0004 §3-4).
    """
    validate_patch(patch)
    if patch and user_id:
        ui_settings_store.upsert(
            user_id,
            columns=patch,
            unset_columns=defaults(),
            updated_at=now_iso(),
        )
    return settings_response(user_id)


def settings_response(user_id: Optional[str]) -> dict:
    """The envelope both /me/ui-settings verbs answer with."""
    settings, is_default = resolve_ui_settings(user_id)
    stored_defaults = defaults()
    stored_defaults.pop("updated_at", None)
    return {
        "ok": True,
        "settings": settings,
        "is_default": is_default,
        "defaults": stored_defaults,
        "domain": domain(),
    }


def settings_response_safe(user_id: Optional[str]) -> dict:
    """``settings_response`` that survives an unreachable store (L0003 §2-8 last line).

    A 500 here would take the finished-card feature down with the settings table.  The
    fallback answers the defaults envelope and says so, so the screen still renders the
    nine choices and the store still gets a value to sweep with.
    """
    try:
        return settings_response(user_id)
    except Exception:
        _log.warning("ui settings lookup failed; answering defaults (user_id=%s)", user_id)
        return {
            "ok": True,
            "settings": defaults(),
            "is_default": True,
            "defaults": {RETENTION_FIELD: RETENTION_DEFAULT_MINUTES},
            "domain": domain(),
        }
