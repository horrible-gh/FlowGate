"""flowgate.v02test.0053 0003-T (1-A): the two system defaults of 검증 (self-check).

``verify_max_runs`` (default 3) and ``verify_run_timeout_sec`` (default 180) are K/V system settings. As in
test_ai_repeat_count_max_0490, the settings table is an in-memory dict; the real getters, validators, service and
router run unchanged.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
os.environ.setdefault("ALLOWED_ORIGIN", "http://localhost")
os.environ.setdefault("CONTEXT", "/flowgate")
os.environ.setdefault("DB_TYPE", "sqlite")
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import pytest
from fastapi import HTTPException

from modules.flow_gate.db import system_settings as db_system_settings
from modules.flow_gate.settings import system_settings_service as service
from modules.flow_gate.settings import verify_policy as policy
from modules.flow_gate.settings.routers import system as system_router

RUNS, TIMEOUT = "verify_max_runs", "verify_run_timeout_sec"


@pytest.fixture
def fake_settings(monkeypatch):
    store: dict[str, dict] = {}

    def _set_value(key, value, value_type="string", description=None, updated_by=None):
        row = {"setting_key": key, "setting_value": value, "value_type": value_type, "description": description,
               "updated_at": "2026-11-19T00:00:00+09:00", "updated_by": updated_by}
        store[key] = row
        return row

    monkeypatch.setattr(db_system_settings, "get", store.get)
    monkeypatch.setattr(db_system_settings, "get_value", lambda key, default=None: store[key]["setting_value"] if key in store else default)
    monkeypatch.setattr(db_system_settings, "list_settings", lambda: list(store.values()))
    monkeypatch.setattr(db_system_settings, "set_value", _set_value)
    return store


def test_the_defaults_are_three_runs_and_one_hundred_eighty_seconds_everywhere(fake_settings):
    assert (policy.get_max_runs(), policy.get_run_timeout_sec()) == (3, 180)
    shown = {r["setting_key"]: r for r in service.get_all()}
    assert (shown[RUNS]["setting_value"], shown[RUNS]["value_type"]) == ("3", "integer")
    assert (shown[TIMEOUT]["setting_value"], shown[TIMEOUT]["value_type"]) == ("180", "integer")
    assert service.get_one(RUNS)["setting_value"] == "3" and service.get_one(TIMEOUT)["setting_value"] == "180"
    assert RUNS in service.ALLOWLIST and TIMEOUT in service.ALLOWLIST
    assert fake_settings == {}                                  # showing a default stores nothing


def test_a_saved_value_is_what_the_getters_and_the_list_answer(fake_settings):
    results = service.set_values({RUNS: "5", TIMEOUT: "600"}, updated_by="u1")
    assert [(r["setting_key"], r["setting_value"], r["value_type"]) for r in results] == [
        (RUNS, "5", "integer"), (TIMEOUT, "600", "integer")]
    assert (policy.get_max_runs(), policy.get_run_timeout_sec()) == (5, 600)
    shown = {r["setting_key"]: r["setting_value"] for r in service.get_all()}
    assert (shown[RUNS], shown[TIMEOUT]) == ("5", "600")
    service.set_values({TIMEOUT: "1800"})
    assert policy.get_run_timeout_sec() == 1800                 # the self-check run's own ceiling is allowed


@pytest.mark.parametrize("value, expected", [("1", 1), ("999", 999), ("1000", 1000), ("100000", 100000)])
def test_verify_max_runs_has_no_upper_bound(fake_settings, value, expected):
    service.set_values({RUNS: value})                           # 0003-T: 'a whole number of 1 or more'
    assert policy.get_max_runs() == expected
    assert service.get_one(RUNS)["setting_value"] == value


@pytest.mark.parametrize("value", ["0", "-1", "abc", "", "3.5", True, None])
def test_a_bad_verify_max_runs_is_refused_and_nothing_is_saved(fake_settings, value):
    with pytest.raises(ValueError, match="verify_max_runs must be an integer of 1 or more"):
        service.set_values({RUNS: value})
    assert fake_settings == {}


@pytest.mark.parametrize("value", ["0", "59", "90", "1860", "3600", "abc", "", True, None])
def test_a_bad_verify_run_timeout_is_refused_and_nothing_is_saved(fake_settings, value):
    with pytest.raises(ValueError, match="verify_run_timeout_sec must be whole minutes"):
        service.set_values({TIMEOUT: value})
    assert fake_settings == {}


@pytest.mark.parametrize("raw", ["0", "-4", "abc", "", "  ", "2.5"])
def test_a_corrupt_stored_runs_value_falls_back_to_the_default(fake_settings, raw):
    fake_settings[RUNS] = {"setting_key": RUNS, "setting_value": raw, "value_type": "integer",
                           "description": None, "updated_at": "t", "updated_by": None}
    assert policy.get_max_runs() == 3
    assert service.get_one(RUNS)["setting_value"] == "3"
    assert {r["setting_key"]: r["setting_value"] for r in service.get_all()}[RUNS] == "3"


@pytest.mark.parametrize("raw", ["0", "30", "90", "1860", "abc", ""])
def test_a_corrupt_stored_timeout_falls_back_to_the_default(fake_settings, raw):
    fake_settings[TIMEOUT] = {"setting_key": TIMEOUT, "setting_value": raw, "value_type": "integer",
                              "description": None, "updated_at": "t", "updated_by": None}
    assert policy.get_run_timeout_sec() == 180
    assert service.get_one(TIMEOUT)["setting_value"] == "180"


def test_the_getters_never_raise_when_the_settings_table_fails(monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("db down")

    monkeypatch.setattr(db_system_settings, "get_value", boom)
    assert (policy.get_max_runs(), policy.get_run_timeout_sec()) == (3, 180)


def test_the_router_answers_a_stable_code_for_each_refusal(fake_settings):
    def refuse(updates):
        with pytest.raises(HTTPException) as caught:
            system_router.update_settings(system_router.SettingsPatch(updates=updates), {"user_id": "usr_admin"})
        assert caught.value.status_code == 422
        return caught.value.detail

    assert refuse({RUNS: "0"}) == {"code": "verify_max_runs_out_of_range", "params": {"min": 1}}
    assert refuse({TIMEOUT: "90"}) == {"code": "verify_run_timeout_out_of_range",
                                       "params": {"min": 60, "max": 1800, "step": 60}}
    ok = system_router.update_settings(system_router.SettingsPatch(updates={RUNS: "10", TIMEOUT: "300"}),
                                       {"user_id": "usr_admin"})
    assert [(r["setting_key"], r["setting_value"]) for r in ok["updated"]] == [(RUNS, "10"), (TIMEOUT, "300")]
