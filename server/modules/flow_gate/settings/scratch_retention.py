"""Global filesystem retention shared by run and token scratch GC.

The persisted value and unit remain separate. Only GC converts them to a
``timedelta``; token credential expiry has its own policy.
"""
from __future__ import annotations

import re
from datetime import timedelta

from modules.flow_gate.db import system_settings as db_settings

VALUE_KEY = "scratch_ttl_value"
UNIT_KEY = "scratch_ttl_unit"
DEFAULT_VALUE = "7"
DEFAULT_UNIT = "day"
MINUTES_PER_UNIT = {"minute": 1, "hour": 60, "day": 1440}


def parse(value: str, unit: str) -> timedelta:
    if not isinstance(value, str) or re.fullmatch(r"[1-9][0-9]*", value) is None:
        raise ValueError("scratch_ttl_value must be a positive integer (minimum 1 minute)")
    if not isinstance(unit, str) or unit not in MINUTES_PER_UNIT:
        raise ValueError("scratch_ttl_unit must be one of: minute, hour, day")
    try:
        return timedelta(minutes=int(value) * MINUTES_PER_UNIT[unit])
    except OverflowError as exc:
        raise ValueError("scratch_ttl_value is too large") from exc


def effective_pair() -> tuple[str, str]:
    # One SELECT observes a consistent pair when a settings PATCH writes both
    # keys in one transaction. A legacy DB with neither key uses seven days.
    rows = {row["setting_key"]: row["setting_value"] for row in db_settings.list_settings()}
    value = rows.get(VALUE_KEY)
    unit = rows.get(UNIT_KEY)
    if value is None and unit is None:
        return DEFAULT_VALUE, DEFAULT_UNIT
    parse(value, unit)  # partial or corrupt settings fail closed in the GC
    return value, unit


def effective_retention() -> timedelta:
    return parse(*effective_pair())
