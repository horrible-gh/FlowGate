"""Independently selectable scenarios; shared fixtures remain in test_system_general_users_0076.py."""
from __future__ import annotations

from tests.modules.flow_gate.test_system_general_users_0076 import (
    API,
    DELEGATED,
    D_ADMIN_FLAG_OBSERVED,
    D_OBSERVED,
    Env,
    PASSWORD,
    SCRATCH_KEYS,
    WATCHED,
    _delegate,
    _fail_second_settings_write,
    _raw_settings,
    _send,
    _settings_map,
    _snapshot,
    _system_calls,
    contextmanager,
    env,
    os,
    pytest,
    sqlite3,
)

def test_v06_d_delegated_observation_administrator_flag_changes(env):
    """Observation of what system.user.update lets a delegated non-admin do to administrator flags:
    raise itself to administrator and disable an administrator. Recorded as the existing server
    behaviour for the 0075 RBAC follow-up; not changed here and not a release basis."""
    _delegate(env)
    d = env.h("D")
    self_admin = env.client.put(f"{API}/system/users/usr_dd0076/admin", json={"is_admin": True}, headers=d)
    users = {u["user_id"]: u for u in env.client.get(f"{API}/system/users", headers=env.h("A")).json()["users"]}
    print(f"V06 D(delegated) PUT own admin=true: {self_admin.status_code} {self_admin.json()}; D is_admin now={users['usr_dd0076']['is_admin']}")
    disable_admin = env.client.post(f"{API}/system/users/usr_x0076/disable", json={}, headers=d)
    users = {u["user_id"]: u for u in env.client.get(f"{API}/system/users", headers=env.h("A")).json()["users"]}
    print(f"V06 D(delegated) disables administrator X: {disable_admin.status_code}; X state now={users['usr_x0076']['state']}")
    assert (self_admin.status_code, users["usr_dd0076"]["is_admin"]) == D_ADMIN_FLAG_OBSERVED["self_admin"]
    assert (disable_admin.status_code, users["usr_x0076"]["state"]) == D_ADMIN_FLAG_OBSERVED["disable_admin"]
def test_v11_storage_root_reports_stored_and_effective_separately(env, monkeypatch, tmp_path):
    stored = str(tmp_path / "stored-root")
    shadowing = str(tmp_path / "env-root")
    env.store._execute("UPDATE system_settings SET setting_value = ? WHERE setting_key = 'storage_root'", [stored])
    if env.store._fetch_one("SELECT 1 AS ok FROM system_settings WHERE setting_key = 'storage_root'") is None:
        env.store._execute("INSERT INTO system_settings (setting_key, setting_value, value_type, updated_at) VALUES ('storage_root', ?, 'string', '2026-10-20T00:00:00+09:00')", [stored])
    monkeypatch.setenv("FLOWGATE_STORAGE_DIR", shadowing)
    r = env.client.get(f"{API}/system/storage-root", headers=env.h("A"))
    assert r.status_code == 200
    body = r.json()
    print(f"V11 GET /system/storage-root: {body}")
    assert body["configured_value"] == stored
    assert body["effective_value"] == shadowing
    assert body["effective_source"] == "env"
    assert body["env_override"] is True
    assert body["project_id"] is None and body["project_override"] is None
    monkeypatch.delenv("FLOWGATE_STORAGE_DIR")
    body = env.client.get(f"{API}/system/storage-root", headers=env.h("A")).json()
    assert body["effective_value"] == stored and body["effective_source"] == "system_setting"
    assert body["env_override"] is False
def test_v12_scratch_pair_saved_in_one_patch_and_invalid_input_is_422_with_both_keys_unchanged(env):
    a = env.h("A")
    first = _settings_map(env.client.get(f"{API}/system/settings", headers=a).json())
    assert (first["scratch_ttl_value"], first["scratch_ttl_unit"]) == ("7", "day")
    r = env.client.patch(f"{API}/system/settings", json={"updates": {"scratch_ttl_value": "6", "scratch_ttl_unit": "hour"}}, headers=a)
    assert r.status_code == 200
    updated = {row["setting_key"]: (row["setting_value"], row["value_type"]) for row in r.json()["updated"]}
    print(f"V12 PATCH 6 hour → {r.status_code} updated={updated}")
    assert updated == {"scratch_ttl_value": ("6", "integer"), "scratch_ttl_unit": ("hour", "string")}
    again = _settings_map(env.client.get(f"{API}/system/settings", headers=a).json())
    assert (again["scratch_ttl_value"], again["scratch_ttl_unit"]) == ("6", "hour")
    assert _raw_settings(env.db_path) == ("6", "hour")

    refused = []
    for value, unit in (("0", "hour"), ("-1", "hour"), ("1.5", "hour"), ("6", "week"), ("999999999999999999", "day")):
        r = env.client.patch(f"{API}/system/settings", json={"updates": {"scratch_ttl_value": value, "scratch_ttl_unit": unit}}, headers=a)
        refused.append((value, unit, r.status_code, r.json().get("detail")))
        assert r.status_code == 422
        assert _raw_settings(env.db_path) == ("6", "hour")
    print(f"V12 refused: {refused}")
    after = _settings_map(env.client.get(f"{API}/system/settings", headers=a).json())
    assert (after["scratch_ttl_value"], after["scratch_ttl_unit"]) == ("6", "hour")
def test_v13_deployment_store_has_a_real_transaction(env):
    from sqloader.sqlite3 import SQLiteWrapper

    from modules.flow_gate.db import connection
    from modules.flow_gate.settings import system_settings_service

    store = system_settings_service._connection.get_store()
    assert store is env.store and type(store) is connection.FlowGateStore
    assert isinstance(store._db, SQLiteWrapper)
    # The service only falls back to nullcontext when getattr(store, "transaction") is None.
    assert callable(getattr(store, "transaction", None))
