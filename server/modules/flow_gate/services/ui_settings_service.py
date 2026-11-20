"""Per-user, non-chat UI settings — the finished-card retention (0452, L0003) and the
Designer's three dock sizes (flowgate.v02test.0056, see "Designer dock sizes" below).

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

from modules.flow_gate.db import user_designer_layout as designer_layout_store
from modules.flow_gate.db import user_ui_settings as ui_settings_store
from modules.flow_gate.db.connection import now_iso

_log = logging.getLogger(__name__)

# ── L0003 §1-1 numeric parameters ───────────────────────────────────────────
# The closed list, in the order the screen draws it.  -1 = never expires,
# 0 = no finished card at all; the rest are minutes.
RETENTION_DOMAIN_MINUTES = (-1, 0, 30, 60, 120, 180, 360, 720, 1440)
RETENTION_DEFAULT_MINUTES = -1
RETENTION_NEVER = -1
RETENTION_IMMEDIATE = 0

# ── L0003 §1-2 identifiers ──────────────────────────────────────────────────
# The stored column name, the response field name and the client type's key are all this
# one string.  A translation table between layers is the accident, not the safeguard.
RETENTION_FIELD = "ai_finished_card_retention_minutes"

# ── Designer dock sizes (flowgate.v02test.0056, R 0056.0001 §2) ─────────────────────
# Left panel width, right panel width and bottom panel height of the Designer, in px.
# Unlike the retention these are ranges, not a closed list: the minimum is the smallest
# size the screen lets a panel be dragged to, the maximum only keeps nonsense out.  The
# screen shows a stored size smaller when the window has no room for it and never writes
# that smaller size back, so no "fits the window" rule belongs here.
#
# They live in their own table (migration 206).  ``is_default`` therefore keeps meaning
# "never chose a retention": dragging a panel does not create the retention row.
DESIGNER_LEFT_WIDTH_FIELD = "designer_left_width"
DESIGNER_RIGHT_WIDTH_FIELD = "designer_right_width"
DESIGNER_BOTTOM_HEIGHT_FIELD = "designer_bottom_height"
DESIGNER_SIZE_MAX = 4000

# field -> (stored column, default, minimum, maximum)
DESIGNER_SIZE_FIELDS = {
    DESIGNER_LEFT_WIDTH_FIELD: ("left_width", 280, 200, DESIGNER_SIZE_MAX),
    DESIGNER_RIGHT_WIDTH_FIELD: ("right_width", 300, 240, DESIGNER_SIZE_MAX),
    DESIGNER_BOTTOM_HEIGHT_FIELD: ("bottom_height", 320, 160, DESIGNER_SIZE_MAX),
}

# Validation order is fixed so a request with several bad fields always reports the same
# one.
PATCH_FIELDS = (RETENTION_FIELD, *DESIGNER_SIZE_FIELDS)


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
        **designer_size_defaults(),
        "updated_at": None,
    }


def domain() -> dict:
    """The choices the response carries so no screen holds its own copy (L0003 §2-8).

    The retention is a list of choices; a Designer size is a ``{"min", "max"}`` range.
    """
    return {
        RETENTION_FIELD: list(RETENTION_DOMAIN_MINUTES),
        **{
            field: {"min": minimum, "max": maximum}
            for field, (_column, _default, minimum, maximum) in DESIGNER_SIZE_FIELDS.items()
        },
    }


def designer_size_defaults() -> dict:
    """The Designer sizes of somebody who never dragged a panel (280 / 300 / 320)."""
    return {field: spec[1] for field, spec in DESIGNER_SIZE_FIELDS.items()}


def normalize_designer_size(field: str, value: Any) -> int:
    """Repair a stored Designer size on the way out: outside the range means the default."""
    _column, default, minimum, maximum = DESIGNER_SIZE_FIELDS[field]
    if value is None or isinstance(value, bool) or not isinstance(value, int):
        return default
    if value < minimum or value > maximum:
        return default
    return value


def resolve_designer_sizes(user_id: Optional[str]) -> dict:
    """The three Designer sizes for a user.  Never fails and never writes.

    No row, an unreadable store or a strange stored value all answer the default: a size
    must not be able to stop the settings from being read (same rule as L0003 §5).
    """
    if not user_id:
        return designer_size_defaults()
    try:
        row = designer_layout_store.get(user_id)
    except Exception:
        _log.warning("designer layout unavailable; answering defaults (user_id=%s)", user_id)
        return designer_size_defaults()
    if row is None:
        return designer_size_defaults()
    return {
        field: normalize_designer_size(field, row.get(spec[0]))
        for field, spec in DESIGNER_SIZE_FIELDS.items()
    }


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

    # ``is_default`` and ``updated_at`` are the retention row's alone; the Designer sizes
    # come from their own row and do not change either.
    row = ui_settings_store.get(user_id)
    sizes = resolve_designer_sizes(user_id)
    if row is None:
        return {**defaults(), **sizes}, True

    return {
        RETENTION_FIELD: normalize_retention_minutes(row.get(RETENTION_FIELD)),
        **sizes,
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
        if field in DESIGNER_SIZE_FIELDS:
            _column, _default, minimum, maximum = DESIGNER_SIZE_FIELDS[field]
            if value < minimum or value > maximum:
                raise UiSettingsError(
                    field, f"{field} must be between {minimum} and {maximum}."
                )
            continue
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
    # Each field goes to its own row: a size-only patch must not create the retention row
    # (that would flip ``is_default``), and a retention-only patch writes no size.
    retention = {key: value for key, value in patch.items() if key == RETENTION_FIELD}
    sizes = {
        DESIGNER_SIZE_FIELDS[key][0]: value
        for key, value in patch.items()
        if key in DESIGNER_SIZE_FIELDS
    }
    if retention and user_id:
        ui_settings_store.upsert(
            user_id,
            columns=retention,
            unset_columns={RETENTION_FIELD: RETENTION_DEFAULT_MINUTES},
            updated_at=now_iso(),
        )
    if sizes and user_id:
        designer_layout_store.upsert(
            user_id,
            columns=sizes,
            unset_columns={spec[0]: spec[1] for spec in DESIGNER_SIZE_FIELDS.values()},
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
            "defaults": {RETENTION_FIELD: RETENTION_DEFAULT_MINUTES, **designer_size_defaults()},
            "domain": domain(),
        }
