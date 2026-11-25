"""System-wide title provider policy; never a member of an AI group."""
from modules.flow_gate.db import system_settings, ai_providers
from modules.flow_gate.settings import ai_settings_service

KEY = "ai_title_provider"


def default_provider():
    providers = ai_settings_service.resolve_global().get("providers", [])
    local = [p for p in providers if p.get("exec_type") == "api" or
             (p.get("exec_type") == "cli" and p.get("exec_location") != "remote")]
    for p in local:
        label = " ".join(str(p.get(k) or "") for k in ("name", "api_model", "cli_command")).lower()
        if any(word in label for word in ("haiku", "mini", "nano", "flash", "small")):
            return p["id"]
    return "off"


def value():
    stored = system_settings.get(KEY)
    return stored["setting_value"] if stored else default_provider()


def row():
    return {"setting_key": KEY, "setting_value": value(), "value_type": "string",
            "description": "AI used for automatic titles", "updated_at": "", "updated_by": None}


def validate(value):
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError("ai_title_provider must be off, executor or an enabled provider id")
    if value in {"off", "executor"}:
        return
    provider = ai_providers.get_public_row(value)
    if not provider or not provider.get("enabled"):
        raise ValueError("ai_title_provider must be off, executor or an enabled provider id")


def selected(executor, user_id=None):
    from modules.flow_gate.db import user_ai_preferences
    personal = user_ai_preferences.get(user_id)["title_provider"] if user_id else "auto"
    setting = value() if personal == "auto" else personal
    return None if setting == "off" else executor if setting == "executor" else setting
