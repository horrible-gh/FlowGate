"""Limits of AI attachment upload requests: system defaults, per-project overrides, hard caps.

flowgate.chat.0017 CH0002 final design §8. Resolution of one key::

    effective = clamp(project override ?? system value ?? DEFAULT, MIN, HARD_MAX)

* The system value is one JSON object in ``system_settings`` (key ``attachment_request_policy``).
  Only the keys an administrator saved are in it.
* A project override (``project_attachment_request_settings``) stores only the keys that project
  sets; a key it does not set inherits the system value. Clearing a key returns it to inheritance.
* ``HARD_MAX`` is the server's safety ceiling and is never exceeded, whatever is stored. A stored
  value that is not usable (corrupt JSON, wrong type, out of range) falls back to the next level
  instead of failing a request.

These limits apply to AI requests only. A person's own upload keeps the fixed constants in
``constants.py``; the file-size and per-document ceilings here can never go above those constants,
because an approval registers the file through the same attachment registry.
"""
from __future__ import annotations

import json
from typing import Any, Optional

from .constants import ATTACH_MAX_COUNT_PER_DOC, ATTACH_MAX_FILENAME_BYTES, ATTACH_MAX_UPLOAD_BYTES

SYSTEM_KEY = "attachment_request_policy"

MIB = 1024 * 1024

# key -> (default, minimum, hard maximum)
INT_LIMITS: dict[str, tuple[int, int, int]] = {
    "max_file_bytes": (ATTACH_MAX_UPLOAD_BYTES, 1, ATTACH_MAX_UPLOAD_BYTES),
    "max_attachments_per_doc": (ATTACH_MAX_COUNT_PER_DOC, 1, ATTACH_MAX_COUNT_PER_DOC),
    "max_pending_per_target": (10, 1, 50),
    "max_requests_per_run": (20, 1, 100),
    "max_pending_bytes_per_project": (100 * MIB, 1 * MIB, 1024 * MIB),
    "max_filename_bytes": (ATTACH_MAX_FILENAME_BYTES, 16, ATTACH_MAX_FILENAME_BYTES),
    "request_retention_days": (7, 1, 30),
    "backup_retention_days": (7, 1, 30),
}
LIST_KEY = "blocked_extensions"
LIST_MAX_ENTRIES = 100
LIST_ENTRY_MAX_CHARS = 32

KEYS = tuple(INT_LIMITS) + (LIST_KEY,)


class SettingsError(ValueError):
    def __init__(self, field: str, message: str):
        super().__init__(message)
        self.field = field
        self.message = message


def defaults() -> dict:
    out: dict[str, Any] = {key: spec[0] for key, spec in INT_LIMITS.items()}
    out[LIST_KEY] = []
    return out


def bounds() -> dict:
    """What a settings screen shows next to each input."""
    out: dict[str, Any] = {key: {"default": d, "min": lo, "max": hi} for key, (d, lo, hi) in INT_LIMITS.items()}
    out[LIST_KEY] = {"default": [], "max_entries": LIST_MAX_ENTRIES}
    return out


def _normalize_extension(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    ext = value.strip().lstrip(".").lower()
    if not ext or len(ext) > LIST_ENTRY_MAX_CHARS or any(ch in ext for ch in "/\\ \t\r\n"):
        return None
    return ext


def _usable_int(key: str, value: Any) -> Optional[int]:
    _default, lo, hi = INT_LIMITS[key]
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if value < lo or value > hi:
        return None
    return int(value)


def _usable_list(value: Any) -> Optional[list[str]]:
    if not isinstance(value, list) or len(value) > LIST_MAX_ENTRIES:
        return None
    out: list[str] = []
    for item in value:
        ext = _normalize_extension(item)
        if ext is None:
            return None
        if ext not in out:
            out.append(ext)
    return out


def _usable(key: str, value: Any):
    return _usable_list(value) if key == LIST_KEY else _usable_int(key, value)


def validate(values: Any, *, allow_null: bool) -> dict:
    """Strictly validate a PUT body. ``allow_null`` (project override) lets ``null`` mean
    "inherit"; nulls are dropped from the returned dict. Unknown keys are refused."""
    if not isinstance(values, dict):
        raise SettingsError("values", "settings must be an object")
    unknown = sorted(set(values) - set(KEYS))
    if unknown:
        raise SettingsError(unknown[0], f"unknown setting: {unknown[0]}")
    out: dict[str, Any] = {}
    for key, value in values.items():
        if value is None:
            if allow_null:
                continue
            raise SettingsError(key, f"{key} is required")
        usable = _usable(key, value)
        if usable is None:
            if key == LIST_KEY:
                raise SettingsError(key, f"{key} must be a list of at most {LIST_MAX_ENTRIES} extensions")
            _d, lo, hi = INT_LIMITS[key]
            raise SettingsError(key, f"{key} must be an integer between {lo} and {hi}")
        out[key] = usable
    return out


def _parse(raw: Optional[str]) -> dict:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _clean(stored: dict) -> dict:
    """The usable keys of a stored object; anything corrupt is dropped (inherits)."""
    out: dict[str, Any] = {}
    for key in KEYS:
        if key in stored:
            usable = _usable(key, stored[key])
            if usable is not None:
                out[key] = usable
    return out


def system_values() -> dict:
    """The keys an administrator saved at system level (cleaned)."""
    from modules.flow_gate.db import system_settings as _db

    try:
        return _clean(_parse(_db.get_value(SYSTEM_KEY, None)))
    except Exception:
        return {}


def project_values(project_id: Optional[str]) -> dict:
    if not project_id:
        return {}
    from modules.flow_gate.db import attachment_upload_requests as db

    try:
        row = db.get_project_settings(project_id)
    except Exception:
        return {}
    return _clean((row or {}).get("values") or {})


def effective(project_id: Optional[str] = None) -> dict:
    """Every key, resolved: project ?? system ?? default, inside [min, hard max]."""
    out = defaults()
    out.update(system_values())
    out.update(project_values(project_id))
    return out


def effective_with_sources(project_id: Optional[str] = None) -> dict:
    system = system_values()
    project = project_values(project_id)
    values = effective(project_id)
    sources = {
        key: ("project" if key in project else "system" if key in system else "default")
        for key in KEYS
    }
    return {"values": values, "sources": sources}


def save_system(values: Any, updated_by: Optional[str]) -> dict:
    from modules.flow_gate.db import system_settings as _db

    cleaned = validate(values, allow_null=True)
    _db.set_value(SYSTEM_KEY, json.dumps(cleaned, ensure_ascii=False, sort_keys=True), "string",
                  updated_by=updated_by)
    return cleaned


def save_project(project_id: str, values: Any, updated_by: Optional[str]) -> dict:
    from modules.flow_gate.db import attachment_upload_requests as db

    cleaned = validate(values, allow_null=True)
    db.set_project_settings(project_id, cleaned, updated_by)
    return cleaned


def system_view() -> dict:
    stored = system_values()
    return {"values": {**defaults(), **stored}, "stored": stored, "defaults": defaults(), "bounds": bounds()}


def project_view(project_id: str) -> dict:
    stored = project_values(project_id)
    inherited = {**defaults(), **system_values()}
    data = effective_with_sources(project_id)
    return {
        "project_id": project_id,
        "overrides": stored,
        "inherited": inherited,
        "effective": data["values"],
        "sources": data["sources"],
        "bounds": bounds(),
    }
