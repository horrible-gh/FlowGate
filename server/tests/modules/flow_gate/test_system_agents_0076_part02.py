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

def test_v16_registration_is_remote_enabled_with_capacity_and_workdir(env):
    a=env.h("A")
    assert env.client.post(f"{API}/system/agents",json=LOCAL_BODY,headers=a).status_code==422
    remote=_create(env,{**REMOTE_BODY,"max_concurrent_jobs":8,"remote_workdir":"/jobs","workspace_cleanup_retention_hours":48})
    assert remote["enabled"] and remote["location"]=="remote" and remote["connection_mode"]=="agent_pull"
    assert remote["max_concurrent_jobs"]==8 and remote["remote_workdir"]=="/jobs"
    assert remote["workspace_cleanup_retention_hours"]==48
    assert env.agent(remote["agent_id"]).json()==remote
@pytest.mark.parametrize("body,why", [
    ({**LOCAL_BODY, "connection_mode": "agent_pull"}, "local with a connection mode"),
    ({**REMOTE_BODY, "local_executable_path": "/opt/x"}, "remote with a path"),
    ({**REMOTE_BODY, "connection_mode": None}, "remote without agent_pull"),
    ({**REMOTE_BODY, "host": "10.0.0.1"}, "an extra field (host)"),
    ({k: v for k, v in LOCAL_BODY.items() if k != "connection_mode"}, "connection_mode left out"),
    ({**REMOTE_BODY, "name": "   "}, "a blank name"),
    ({**REMOTE_BODY, "configured_capabilities": {"ai_cli": True, "ai_api": True}}, "a missing capability"),
])
def test_v16_register_refuses_other_shapes(env, body, why):
    before = _snapshot(env.db_path, AGENT_TABLES)
    r = env.client.post(f"{API}/system/agents", json=body, headers=env.h("A"))
    print(f"V16 POST with {why}: {r.status_code}")
    assert r.status_code == 422, r.text
    assert _snapshot(env.db_path, AGENT_TABLES) == before
def test_v16_patch_changed_fields_only_location_immutable_and_enabled_toggle(env):
    a = env.h("A")
    aid = _create(env, REMOTE_BODY)["agent_id"]
    r = env.client.patch(f"{API}/system/agents/{aid}", json={"name": "renamed"}, headers=a)
    assert r.status_code == 200 and r.json()["name"] == "renamed"
    assert r.json()["configured_capabilities"] == {"ai_cli":True,"ai_api":True,"storage":False}  # untouched
    r = env.client.patch(f"{API}/system/agents/{aid}", json={"configured_capabilities": {"ai_cli": False, "ai_api": True, "storage": False}}, headers=a)
    assert r.status_code == 422
    loc = env.client.patch(f"{API}/system/agents/{aid}", json={"location": "local"}, headers=a)
    assert loc.status_code == 422
    assert env.client.patch(f"{API}/system/agents/{aid}", json={}, headers=a).status_code == 422
    off = env.client.patch(f"{API}/system/agents/{aid}", json={"enabled": False}, headers=a)
    assert off.status_code == 200 and off.json()["enabled"] is False
    assert env.agent(aid).json()["enabled"] is False
    on = env.client.patch(f"{API}/system/agents/{aid}", json={"enabled": True}, headers=a)
    assert on.status_code == 200 and env.agent(aid).json()["enabled"] is True
    got = env.agent(aid).json()
    print(f"V16 PATCH name/caps 200, location 422 immutable, {{}} 422, enabled false/true 200 -> {got['name']}, {got['location']}, {got['configured_capabilities']}")
    assert got["location"] == "remote" and got["connection_mode"] == "agent_pull"
