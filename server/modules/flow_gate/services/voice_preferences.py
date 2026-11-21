"""User voice preferences; missing/deleted selections resolve at transcription time."""
from __future__ import annotations
import logging
import re
from modules.flow_gate.db.connection import get_store, now_iso

DEFAULTS = {"voice_action": "text", "voice_service": "", "voice_model": "", "voice_realtime": False}


def valid(key, value):
    if key == "voice_action":
        return isinstance(value, str) and value in ("text", "audio")
    if key == "voice_realtime":
        return isinstance(value, bool)
    return isinstance(value, str) and (value == "" or re.fullmatch(r"[a-zA-Z0-9_.-]{1,100}", value) is not None)


def resolve(user_id):
    if not user_id:
        return dict(DEFAULTS)
    try:
        row = get_store()._fetch_one("SELECT * FROM user_voice_settings WHERE user_id = ?", [user_id])
    except Exception:
        logging.getLogger(__name__).warning("Voice preferences unavailable; using defaults")
        return dict(DEFAULTS)
    if row is None:
        return dict(DEFAULTS)
    result = {}
    for key, default in DEFAULTS.items():
        value = row.get(key)
        if key == "voice_realtime":
            value = bool(value) if value in (0, 1) else False
        result[key] = value if valid(key, value) else default
    return result


def save(user_id, patch):
    if not patch or not user_id:
        return
    if any(key in patch for key in ("voice_service", "voice_model")) or patch.get("voice_realtime"):
        from .voice_service import manager, specification
        from .voice_errors import VoiceError
        from .ui_settings_service import UiSettingsError
        effective = {**resolve(user_id), **patch}
        try:
            selected = manager.select(effective["voice_service"] or None, effective["voice_model"] or None)
            if effective["voice_realtime"] and not specification(**selected)["realtime"]:
                raise UiSettingsError("voice_realtime", "Selected model does not support realtime input.")
        except VoiceError as exc:
            # Clearing both choices is valid even before the first install.
            if effective["voice_service"] or effective["voice_model"] or effective["voice_realtime"]:
                raise UiSettingsError("voice_model", exc.message) from exc
    values = {**DEFAULTS, **patch}
    columns = list(DEFAULTS)
    updates = ", ".join(f"{key} = excluded.{key}" for key in columns if key in patch)
    get_store()._execute(
        "INSERT INTO user_voice_settings (user_id, " + ", ".join(columns) + ", updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (user_id) DO UPDATE SET " + updates + ", updated_at = excluded.updated_at",
        [user_id, *[int(values[key]) if key == "voice_realtime" else values[key] for key in columns], now_iso()])
