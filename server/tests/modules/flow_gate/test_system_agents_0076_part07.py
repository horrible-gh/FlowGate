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

def test_workspace_cleanup_policy_reaches_the_agent_on_its_next_heartbeat_0019(env):
    a = env.h("A")
    aid, credential = _registered(env, "policy-probe")
    assert env.heartbeat(aid, credential).json()["workspace_cleanup"] == {"enabled": True, "retention_hours": 24}
    env.client.patch(f"{API}/system/agents/{aid}", json={"workspace_cleanup_enabled": False, "workspace_cleanup_retention_hours": 48}, headers=a)
    assert env.heartbeat(aid, credential).json()["workspace_cleanup"] == {"enabled": False, "retention_hours": 48}
@pytest.mark.parametrize("field,value", [
    ("workspace_cleanup_retention_hours", 0), ("workspace_cleanup_retention_hours", 721),
    ("workspace_cleanup_retention_hours", 1.5), ("workspace_cleanup_retention_hours", "24"),
    ("workspace_cleanup_retention_hours", True), ("workspace_cleanup_retention_hours", None),
    ("workspace_cleanup_enabled", "true"), ("workspace_cleanup_enabled", 1), ("workspace_cleanup_enabled", None),
])
def test_workspace_cleanup_refuses_invalid_values_0019(env, field, value):
    a = env.h("A")
    aid = _create(env, REMOTE_BODY)["agent_id"]
    before = _snapshot(env.db_path, AGENT_TABLES)
    patched = env.client.patch(f"{API}/system/agents/{aid}", json={field: value}, headers=a)
    created = env.client.post(f"{API}/system/agents", json={**REMOTE_BODY, field: value}, headers=a)
    assert (patched.status_code, created.status_code) == (422, 422), (patched.text, created.text)
    assert _snapshot(env.db_path, AGENT_TABLES) == before
def test_workspace_cleanup_accepts_the_range_ends_with_cleanup_off_0019(env):
    a = env.h("A")
    aid = _create(env, REMOTE_BODY)["agent_id"]
    for hours in (1, 720):
        r = env.client.patch(f"{API}/system/agents/{aid}", json={"workspace_cleanup_enabled": False, "workspace_cleanup_retention_hours": hours}, headers=a)
        assert r.status_code == 200 and r.json()["workspace_cleanup_retention_hours"] == hours
def test_service_validation_refuses_what_the_request_model_would_have_caught_0019(env):
    from modules.flow_gate.services.agent_registry import AgentError, AgentService
    svc = AgentService(env.store, owner_user_id=None)
    aid = _create(env, REMOTE_BODY)["agent_id"]
    for body in ({"workspace_cleanup_retention_hours": True}, {"workspace_cleanup_retention_hours": 721},
                 {"workspace_cleanup_enabled": 1}):
        with pytest.raises(AgentError) as e:
            svc.patch(aid, body)
        assert (e.value.status, e.value.code) == (422, "validation_failed")
        assert e.value.details["field"] in body
