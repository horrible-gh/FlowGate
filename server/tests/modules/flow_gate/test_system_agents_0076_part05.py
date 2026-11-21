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

def test_v17_revoke_invalidates_the_credential_and_the_pending_token(env):
    a = env.h("A")
    aid, credential = _registered(env, "revoke-probe")
    pending_token = env.client.post(f"{API}/system/agents/{aid}/enrollments", json={}, headers=a).json()["enrollment_token"]
    assert env.client.post(f"{API}/system/agents/{aid}/credential/revoke", headers=a).status_code == 422  # body required
    r = env.client.post(f"{API}/system/agents/{aid}/credential/revoke", json={}, headers=a)
    assert r.status_code == 200
    after = env.agent(aid).json()
    assert (after["credential_status"], after["enrollment_status"], after["registration_status"]) == ("revoked", "none", "revoked")
    hb = env.heartbeat(aid, credential)
    enroll = env.enroll(pending_token)
    print(f"V17 revoke: 200 -> credential {after['credential_status']}, enrollment {after['enrollment_status']}; old credential heartbeat {hb.status_code}; old pending token enroll {enroll.status_code}")
    assert hb.status_code == 401 and enroll.status_code == 409
def test_v17_delete_with_registration_history_removes_it_from_the_list_and_keeps_history_0019(env):
    # flowgate.v02test.0019: registration history no longer blocks deleting (was 409
    # agent_has_registration_history). The row, its ID and the history stay; deleted_at is set.
    a = env.h("A")
    unregistered = _create(env, REMOTE_BODY)["agent_id"]
    token = env.client.post(f"{API}/system/agents/{unregistered}/enrollments", json={}, headers=a).json()["enrollment_token"]
    r = env.client.delete(f"{API}/system/agents/{unregistered}", headers=a)
    assert r.status_code == 204 and r.content == b""
    gone = env.agent(unregistered)
    assert gone.status_code == 404 and gone.json()["error"]["code"] == "agent_not_found"
    assert unregistered not in env.client.get(f"{API}/system/agents", headers=a).text
    assert env.enroll(token).status_code == 409  # its pending token was revoked with it

    registered, credential = _registered(env, "history-probe")
    env.client.post(f"{API}/system/agents/{registered}/credential/revoke", json={}, headers=a)
    before = _history(env, registered)
    r = env.client.delete(f"{API}/system/agents/{registered}", headers=a)
    assert r.status_code == 204, r.text
    assert env.agent(registered).status_code == 404
    assert registered not in env.client.get(f"{API}/system/agents", headers=a).text
    assert _history(env, registered) == before                       # history rows kept
    row = _rows(env.db_path, "SELECT agent_id, name, deleted_at FROM agents WHERE agent_id=?", (registered,))
    assert row and row[0][1] == "history-probe" and row[0][2] is not None  # same row and ID, marked deleted
    again = env.client.delete(f"{API}/system/agents/{registered}", headers=a)
    assert again.status_code == 404
    reissue = env.client.post(f"{API}/system/agents/{registered}/enrollments", json={}, headers=a)
    patched = env.client.patch(f"{API}/system/agents/{registered}", json={"name": "back"}, headers=a)
    assert (reissue.status_code, patched.status_code) == (404, 404)
    print(f"V17 delete: unregistered 204 -> 404, token 409; revoked history 204 -> 404, history {before} kept")
def test_v17_delete_of_a_connected_agent_revokes_its_credential_0019(env):
    a = env.h("A")
    aid, credential = _registered(env, "live-probe")
    assert env.heartbeat(aid, credential).status_code == 200
    pending = env.client.post(f"{API}/system/agents/{aid}/enrollments", json={}, headers=a).json()["enrollment_token"]
    r = env.client.delete(f"{API}/system/agents/{aid}", headers=a)
    assert r.status_code == 204, r.text
    active = _rows(env.db_path, "SELECT COUNT(*) FROM agent_credentials WHERE agent_id=? AND revoked_at IS NULL", (aid,))[0][0]
    assert active == 0
    hb = env.heartbeat(aid, credential)
    assert hb.status_code == 401 and hb.json()["error"]["code"] == "unauthorized"
    assert env.enroll(pending).status_code == 409
