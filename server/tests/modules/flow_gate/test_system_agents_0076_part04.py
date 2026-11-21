"""Independently selectable scenarios; shared fixtures remain in test_system_agents_0076.py."""
from __future__ import annotations

from tests.modules.flow_gate.test_system_agents_0076 import (
    AGENT_TABLES,
    API,
    D_OBSERVED,
    Env,
    LOCAL_BODY,
    PASSWORD,
    REMOTE_BODY,
    _agent_calls,
    _create,
    _delegate,
    _fixtures,
    _history,
    _job,
    _registered,
    _rows,
    _send,
    _short,
    _snapshot,
    env,
    os,
    pytest,
    sqlite3,
)

def test_v17_reissue_replaces_the_pending_token(env):
    a = env.h("A")
    aid = _create(env, REMOTE_BODY)["agent_id"]
    first = env.client.post(f"{API}/system/agents/{aid}/enrollments", json={}, headers=a).json()["enrollment_token"]
    second = env.client.post(f"{API}/system/agents/{aid}/enrollments", json={}, headers=a).json()["enrollment_token"]
    pending = _rows(env.db_path, "SELECT COUNT(*) FROM agent_enrollments WHERE agent_id=? AND consumed_at IS NULL AND revoked_at IS NULL", (aid,))[0][0]
    assert pending == 1
    old = env.enroll(first)
    new = env.enroll(second)
    print(f"V17 re-issue: pending rows={pending}; first token enroll={old.status_code}, second={new.status_code}")
    assert old.status_code == 409 and old.json()["error"]["code"] == "enrollment_unavailable"
    assert new.status_code == 201
@pytest.mark.parametrize("setup", ["disabled_remote", "local"])
def test_v17_enrollment_only_for_an_enabled_remote_agent(env, setup):
    a = env.h("A")
    if setup == "local":
        # Existing local records remain readable; simulate one without changing its immutable location.
        aid = "legacy-local"
        env.store._execute("INSERT INTO agents(agent_id,name,enabled,location,created_at,updated_at) VALUES(?,?,TRUE,'local',?,?)",[aid,"Legacy local","2026-01-01T00:00:00Z","2026-01-01T00:00:00Z"])
    else:
        aid = _create(env, REMOTE_BODY)["agent_id"]
        assert env.client.patch(f"{API}/system/agents/{aid}",json={"enabled":False},headers=a).status_code==200
    r = env.client.post(f"{API}/system/agents/{aid}/enrollments", json={}, headers=a)
    assert r.status_code == 409 and r.json()["error"]["code"] == "enrollment_not_allowed"
    assert _rows(env.db_path, "SELECT COUNT(*) FROM agent_enrollments")[0][0] == 0
def test_v17_missing_secret_service_answers_503_and_writes_nothing(env, monkeypatch):
    aid = _create(env, REMOTE_BODY)["agent_id"]
    monkeypatch.delenv("FLOWGATE_AGENT_PEPPER")
    before = _snapshot(env.db_path, AGENT_TABLES)
    r = env.client.post(f"{API}/system/agents/{aid}/enrollments", json={}, headers=env.h("A"))
    print(f"V17 issue without FLOWGATE_AGENT_PEPPER: {r.status_code} {r.json()}")
    assert r.status_code == 503 and r.json()["error"]["code"] == "temporarily_unavailable"
    assert _snapshot(env.db_path, AGENT_TABLES) == before
