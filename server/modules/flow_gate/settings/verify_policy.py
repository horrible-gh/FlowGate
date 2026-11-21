"""검증 (self-check) system defaults — flowgate.v02test.0053 0003-T (1-A).

Two system settings are the defaults of every card that does not carry its own value (the card's 실행 AI ⚙ options
``verify_max_runs`` / ``verify_run_minutes``, ``flowgate.card_settings.fields``):

- ``verify_max_runs``: how many verifications one card may use (default 3);
- ``verify_run_timeout_sec``: the longest one verification may run (default 180 seconds). Whole minutes only
  (60..1800: the screen's combo is in minutes, and 1800 seconds is the self-check run's own ceiling,
  ``tr_self_check_service``).

Follows ``ai_execution_policy_service``'s shape on purpose (module constants, public getter / validator, a getter that
never raises): a missing row, a corrupt value or a failing read falls back to the default silently, so a caller
that asks for the limits never fails because of this setting. ``system_settings_service`` imports THIS module for its
save-time check; this module imports only the settings table (no cycle).
"""
from __future__ import annotations

from modules.flow_gate.db import system_settings as _system_settings

RUNS_KEY = "verify_max_runs"
TIMEOUT_KEY = "verify_run_timeout_sec"
DEFAULT_MAX_RUNS = 3
DEFAULT_RUN_TIMEOUT_SEC = 180
MAX_RUNS_MIN = 1                      # no upper bound: the earlier implementation 'a whole number of 1 or more'
TIMEOUT_MIN_SEC, TIMEOUT_MAX_SEC, TIMEOUT_STEP_SEC = 60, 1800, 60
KEYS = (RUNS_KEY, TIMEOUT_KEY)


def _whole(value: object) -> int | None:
    """``value`` as an integer when it is one (an int, or a string of digits with an optional sign), else None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        text = value.strip()
        try:
            return int(text, 10) if text else None
        except ValueError:
            return None
    return None


def valid_max_runs(value: object) -> bool:
    """Is ``value`` an acceptable NEW ``verify_max_runs`` (a whole number of 1 or more)?"""
    number = _whole(value)
    return number is not None and number >= MAX_RUNS_MIN


def valid_run_timeout_sec(value: object) -> bool:
    """Is ``value`` an acceptable NEW ``verify_run_timeout_sec`` (whole minutes, 60..1800 seconds)?"""
    number = _whole(value)
    return (number is not None and TIMEOUT_MIN_SEC <= number <= TIMEOUT_MAX_SEC
            and number % TIMEOUT_STEP_SEC == 0)


def _read(key: str) -> object:
    try:
        return _system_settings.get_value(key)
    except Exception:  # noqa: BLE001 — these getters must never fail a caller
        return None


def get_max_runs() -> int:
    """The system default of a card's verification count. Never raises."""
    raw = _read(RUNS_KEY)
    return int(_whole(raw)) if valid_max_runs(raw) else DEFAULT_MAX_RUNS


def get_run_timeout_sec() -> int:
    """The system default of one verification's longest run, in seconds. Never raises."""
    raw = _read(TIMEOUT_KEY)
    return int(_whole(raw)) if valid_run_timeout_sec(raw) else DEFAULT_RUN_TIMEOUT_SEC


def effective_row(key: str) -> dict | None:
    """The shown row of an unset / corrupt setting (``system_settings_service`` shape), or None for another key."""
    if key == RUNS_KEY:
        return {"setting_key": key, "setting_value": str(get_max_runs()), "value_type": "integer",
                "description": "Default maximum verifications per card", "updated_at": "", "updated_by": None}
    if key == TIMEOUT_KEY:
        return {"setting_key": key, "setting_value": str(get_run_timeout_sec()), "value_type": "integer",
                "description": "Default longest time of one verification (seconds)", "updated_at": "", "updated_by": None}
    return None


def valid_value(key: str, value: object) -> bool:
    return valid_max_runs(value) if key == RUNS_KEY else valid_run_timeout_sec(value)
