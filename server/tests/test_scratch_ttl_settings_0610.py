"""0610 scratch TTL settings: API round trip, validation, and GC conversion."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import timedelta

import pytest
from fastapi import HTTPException

from modules.flow_gate.db import system_settings as db_settings
from modules.flow_gate.settings import scratch_retention, system_settings_service
from modules.flow_gate.settings.routers import system as system_router
from modules.flow_gate.services import token_service


@pytest.fixture
def settings_rows(monkeypatch):
    rows = {}

    def get(key):
        return rows.get(key)

    def get_value(key, default=None):
        return rows[key]["setting_value"] if key in rows else default

    def set_value(key, value, value_type="string", description=None, updated_by=None):
        row = {"setting_key": key, "setting_value": value, "value_type": value_type,
               "description": description, "updated_at": "now", "updated_by": updated_by}
        rows[key] = row
        return row

    @contextmanager
    def transaction():
        snapshot = dict(rows)
        try:
            yield
        except Exception:
            rows.clear()
            rows.update(snapshot)
            raise

    class Store:
        pass

    store = Store()
    store.transaction = transaction
    monkeypatch.setattr(db_settings, "get", get)
    monkeypatch.setattr(db_settings, "get_value", get_value)
    monkeypatch.setattr(db_settings, "list_settings", lambda: list(rows.values()))
    monkeypatch.setattr(db_settings, "set_value", set_value)
    monkeypatch.setattr(system_settings_service._connection, "get_store", lambda: store)
    return rows


def _settings(rows):
    return {row["setting_key"]: row["setting_value"] for row in rows}


@pytest.mark.parametrize("value,unit,minutes", [
    ("30", "minute", 30), ("6", "hour", 360),
    ("1", "day", 1440), ("7", "day", 10080),
    ("1", "minute", 1),
])
def test_api_round_trip_preserves_value_unit_and_converts_only_in_gc(settings_rows, value, unit, minutes):
    assert _settings(system_router.list_settings({"user_id": "admin"})["settings"])["scratch_ttl_value"] == "7"
    assert system_settings_service.get_one("scratch_ttl_unit")["setting_value"] == "day"
    response = system_router.update_settings(
        system_router.SettingsPatch(updates={"scratch_ttl_value": value, "scratch_ttl_unit": unit}),
        {"user_id": "admin"},
    )
    assert _settings(response["updated"]) == {"scratch_ttl_value": value, "scratch_ttl_unit": unit}
    assert _settings(system_router.list_settings({"user_id": "admin"})["settings"])["scratch_ttl_value"] == value
    assert system_settings_service.get_one("scratch_ttl_unit")["setting_value"] == unit
    assert scratch_retention.effective_retention() == timedelta(minutes=minutes)
    assert token_service.TOKEN_TTL_HOURS == 24


@pytest.mark.parametrize("value,unit", [
    ("0", "minute"), ("-1", "hour"), ("1.5", "day"),
    ("abc", "minute"), ("", "day"), (None, "day"),
    ("1", "week"), ("1", "minutes"), ("1", "DAY"),
])
def test_invalid_api_values_rejected_without_writing(settings_rows, value, unit):
    with pytest.raises(HTTPException) as error:
        system_router.update_settings(
            system_router.SettingsPatch(updates={"scratch_ttl_value": value, "scratch_ttl_unit": unit}),
            {"user_id": "admin"},
        )
    assert error.value.status_code == 422
    assert settings_rows == {}


def test_partial_update_uses_saved_other_half_and_invalid_db_fails_closed(settings_rows):
    system_settings_service.set_values({"scratch_ttl_value": "6", "scratch_ttl_unit": "hour"})
    system_settings_service.set_values({"scratch_ttl_value": "30"})
    assert scratch_retention.effective_pair() == ("30", "hour")
    settings_rows["scratch_ttl_unit"]["setting_value"] = "week"
    with pytest.raises(ValueError):
        scratch_retention.effective_retention()
