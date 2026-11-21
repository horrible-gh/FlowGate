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

@pytest.mark.parametrize("status,how", [
    ("CLAIMED", "lease"), ("STARTED", "lease"), ("NEW", "target"),
])
def test_v17_delete_refused_while_jobs_are_in_progress_0019(env, status, how):
    a = env.h("A")
    aid, credential = _registered(env, f"busy-{status.lower()}")
    _job(env, "job_busy", status, target=aid if how == "target" else None, owner=aid if how == "lease" else None)
    before = _snapshot(env.db_path, AGENT_TABLES)
    r = env.client.delete(f"{API}/system/agents/{aid}", headers=a)
    print(f"V17 delete with a {status} job ({how}): {r.status_code} {r.json()}")
    assert r.status_code == 409 and r.json()["error"]["code"] == "agent_has_active_jobs"
    assert _snapshot(env.db_path, AGENT_TABLES) == before            # nothing revoked, nothing marked
    assert env.agent(aid).status_code == 200
    assert env.heartbeat(aid, credential).status_code == 200
@pytest.mark.parametrize("status", ["DONE", "FAILED", "CANCELLED", "TIMED_OUT", "REJECTED"])
def test_v17_finished_jobs_do_not_block_delete_0019(env, status):
    aid, _ = _registered(env, f"done-{status.lower()}")
    _job(env, "job_done", status, target=aid, owner=aid)
    assert env.client.delete(f"{API}/system/agents/{aid}", headers=env.h("A")).status_code == 204
    assert _rows(env.db_path, "SELECT status FROM agent_jobs WHERE job_id='job_done'") == [(status,)]
def test_workspace_cleanup_defaults_saved_read_back_and_changed_0019(env):
    a = env.h("A")
    created = _create(env, REMOTE_BODY)
    aid = created["agent_id"]
    assert (created["workspace_cleanup_enabled"], created["workspace_cleanup_retention_hours"]) == (True, 24)
    r = env.client.patch(f"{API}/system/agents/{aid}", json={"workspace_cleanup_retention_hours": 720}, headers=a)
    assert r.status_code == 200 and (r.json()["workspace_cleanup_enabled"], r.json()["workspace_cleanup_retention_hours"]) == (True, 720)
    r = env.client.patch(f"{API}/system/agents/{aid}", json={"workspace_cleanup_enabled": False}, headers=a)
    assert r.status_code == 200 and (r.json()["workspace_cleanup_enabled"], r.json()["workspace_cleanup_retention_hours"]) == (False, 720)
    r = env.client.patch(f"{API}/system/agents/{aid}", json={"name": "renamed"}, headers=a)
    assert (r.json()["workspace_cleanup_enabled"], r.json()["workspace_cleanup_retention_hours"]) == (False, 720)
    got = env.agent(aid).json()
    assert (got["workspace_cleanup_enabled"], got["workspace_cleanup_retention_hours"]) == (False, 720)
    listed = {x["agent_id"]: x for x in env.client.get(f"{API}/system/agents", headers=a).json()["agents"]}
    assert listed[aid]["workspace_cleanup_retention_hours"] == 720
    other = _create(env, {**REMOTE_BODY, "name": "other", "workspace_cleanup_enabled": False, "workspace_cleanup_retention_hours": 1})
    assert (other["workspace_cleanup_enabled"], other["workspace_cleanup_retention_hours"]) == (False, 1)
    assert env.agent(aid).json()["workspace_cleanup_retention_hours"] == 720   # the other Agent is untouched
