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

def test_remote_workdir_is_saved_read_back_and_cleared_0135(env):
    # per-Agent remote working directory, set / re-read / cleared.
    a = env.h("A")
    created = _create(env, REMOTE_BODY)
    aid = created["agent_id"]
    assert created["remote_workdir"] is None
    r = env.client.patch(f"{API}/system/agents/{aid}", json={"remote_workdir": " /tmp/flowgate/${AGENT_JOB_ID} "}, headers=a)
    assert r.status_code == 200, r.text
    assert r.json()["remote_workdir"] == "/tmp/flowgate/${AGENT_JOB_ID}"
    assert env.agent(aid).json()["remote_workdir"] == "/tmp/flowgate/${AGENT_JOB_ID}"
    assert any(x["agent_id"] == aid and x["remote_workdir"] == "/tmp/flowgate/${AGENT_JOB_ID}"
               for x in env.client.get(f"{API}/system/agents", headers=a).json()["agents"])
    r = env.client.patch(f"{API}/system/agents/{aid}", json={"name": "renamed"}, headers=a)
    assert r.json()["remote_workdir"] == "/tmp/flowgate/${AGENT_JOB_ID}"       # untouched by other changes
    r = env.client.patch(f"{API}/system/agents/{aid}", json={"remote_workdir": ""}, headers=a)
    assert r.status_code == 200 and r.json()["remote_workdir"] is None
    with_dir = _create(env, {**REMOTE_BODY, "name": "with-dir", "remote_workdir": "D:\fg\${AGENT_JOB_ID}"})
    assert with_dir["remote_workdir"] == "D:\fg\${AGENT_JOB_ID}"
def test_v16_online_offline_incompatible_revoked_and_disabled_as_the_get_reports_them(env):
    a = env.h("A")
    aid, credential = _registered(env, "status-probe")
    assert env.heartbeat(aid, credential).status_code == 200
    online = env.agent(aid).json()
    assert (online["online_status"], online["registration_status"], online["credential_status"], online["protocol_compatibility"]) == ("online", "registered", "active", "compatible")
    assert online["reported_capabilities"] == {"ai_cli": True, "ai_api": False, "storage": False}
    assert online["effective_capabilities"] == {"ai_cli": True, "ai_api": False, "storage": False}  # configured ∧ reported
    assert (online["agent_version"], online["os"], online["architecture"], online["protocol_version"]) == ("1.4.2", "linux", "amd64", 1)

    # offline: the last heartbeat is older than the 90 s TTL
    env.store._execute("UPDATE agent_credentials SET last_heartbeat_at='2026-01-01T00:00:00Z' WHERE agent_id=?", [aid])
    assert env.agent(aid).json()["online_status"] == "offline"

    # disabled: heartbeat refused, offline while disabled, registration/credential kept
    assert env.heartbeat(aid, credential).status_code == 200
    env.client.patch(f"{API}/system/agents/{aid}", json={"enabled": False}, headers=a)
    hb = env.heartbeat(aid, credential)
    disabled = env.agent(aid).json()
    assert hb.status_code == 403 and hb.json()["error"]["code"] == "agent_disabled"
    assert (disabled["online_status"], disabled["credential_status"]) == ("offline", "active")
    env.client.patch(f"{API}/system/agents/{aid}", json={"enabled": True}, headers=a)

    # incompatible: a heartbeat with an unsupported protocol
    bad = env.heartbeat(aid, credential, protocol_version=2)
    incompatible = env.agent(aid).json()
    assert bad.status_code == 409
    assert (incompatible["protocol_compatibility"], incompatible["online_status"], incompatible["last_rejected_protocol_version"]) == ("incompatible", "offline", 2)

    # revoked
    assert env.client.post(f"{API}/system/agents/{aid}/credential/revoke", json={}, headers=a).status_code == 200
    revoked = env.agent(aid).json()
    assert (revoked["registration_status"], revoked["credential_status"], revoked["online_status"], revoked["has_registration_history"]) == ("revoked", "revoked", "offline", True)
    print("V16 states via GET: online -> offline(ttl) -> disabled(403 heartbeat, offline, credential active) -> incompatible(409, v2) -> revoked")
def test_v17_enrollment_needs_the_empty_body_and_its_token_is_never_returned_again(env):
    a = env.h("A")
    aid = _create(env, REMOTE_BODY)["agent_id"]
    no_body = env.client.post(f"{API}/system/agents/{aid}/enrollments", headers=a)
    extra = env.client.post(f"{API}/system/agents/{aid}/enrollments", json={"ttl": 60}, headers=a)
    assert (no_body.status_code, extra.status_code) == (422, 422)
    assert _rows(env.db_path, "SELECT COUNT(*) FROM agent_enrollments")[0][0] == 0
    r = env.client.post(f"{API}/system/agents/{aid}/enrollments", json={}, headers=a)
    assert r.status_code == 201 and r.headers["Cache-Control"] == "no-store"
    body = r.json()
    assert set(body) == {"agent_id", "enrollment_token", "expires_at"} and body["agent_id"] == aid
    token = body["enrollment_token"]
    listed = env.client.get(f"{API}/system/agents", headers=a)
    detail = env.agent(aid)
    assert token not in listed.text and token not in detail.text
    assert detail.json()["enrollment_status"] == "pending" and detail.json()["enrollment_expires_at"] == body["expires_at"]
    stored = _rows(env.db_path, "SELECT * FROM agent_enrollments")
    assert all(token not in repr(row) for row in stored)  # digest only
    print(f"V17 enrollments: no body 422, extra field 422, {{}} 201 (keys {sorted(body)}); GET list/detail carry no token; DB keeps a digest only")
