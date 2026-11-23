"""AI attachment read ceiling — a system setting (System > General screen).

GET .../document/{doc_id}/attachments/{name}/read returns the whole file and refuses
(413 READ_TOO_LARGE) rather than truncates, so a few-MB demo video needs the ceiling
raised, not cut. The ceiling is the system setting ``attach_read_max_mb`` (whole MB,
1..64, default 1), saved through PATCH /system/settings and read on every call, so a
change applies at once without a restart.

Only the AI/worker READ ceiling follows this setting. The upload-request preview keeps
its own fixed ATTACH_PREVIEW_MAX_BYTES (1 MiB): raising this value must not grow the
text a person's screen is handed.
"""
from __future__ import annotations

from modules.flow_gate.db import system_settings as _system_settings

SETTING_KEY = "attach_read_max_mb"
DEFAULT_MB = 1
MIN_MB = 1
MAX_MB = 64          # one read holds the file (and its base64) in memory; keep it bounded
BYTES_PER_MB = 1048576
DESCRIPTION = "Largest attachment an AI worker may read (MB)"
OUT_OF_RANGE_MESSAGE = f"{SETTING_KEY} must be an integer between {MIN_MB} and {MAX_MB}"


def _parse(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        candidate = value
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            candidate = int(text, 10)
        except ValueError:
            return None
    else:
        return None
    return candidate if MIN_MB <= candidate <= MAX_MB else None


def valid_setting_value(value: object) -> bool:
    """Is `value` an acceptable NEW attach_read_max_mb (save-time range check)?"""
    return _parse(value) is not None


def get_max_mb() -> int:
    """The effective ceiling in MB. Never raises: a missing row, a bad stored value or a
    store failure all mean the 1 MB default."""
    try:
        value = _parse(_system_settings.get_value(SETTING_KEY))
    except Exception:  # noqa: BLE001 — a read must never fail because of this lookup
        return DEFAULT_MB
    return DEFAULT_MB if value is None else value


def get_max_bytes() -> int:
    return get_max_mb() * BYTES_PER_MB
