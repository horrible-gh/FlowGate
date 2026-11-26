"""Independently selectable scenarios; shared fixtures remain in test_flow_archive_v02test_0002.py."""
from __future__ import annotations

from tests.modules.flow_gate.test_flow_archive_v02test_0002 import (
    FLOW,
    FastAPI,
    JSONResponse,
    Mock,
    Path,
    TestClient,
    ThreadPoolExecutor,
    call,
    env,
    pytest,
    seed_decision,
    seed_run,
    sql,
    sqlite3,
    subprocess,
)

@pytest.mark.parametrize("blocker,reason", [
    ("reservation", "gate_job_unfinished"), ("reset", "recovery_reset_required"),
    ("time", "time_machine"), ("attempt", "work_unfinished"), ("execution", "work_unfinished"),
    ("integration", "work_unfinished"), ("merge", "merge_session_open"),
])
def test_shared_read_only_blockers(env, monkeypatch, blocker, reason):
    from flowgate.gate_decisions import cleanup, repository
    from flowgate.graph_time_machine import operation
    from flowgate.graph_run import execution_store, result_merge
    from modules.flow_gate.graph_workflow.store import run_store
    seed_run(env)
    seed_decision(env)
    if blocker == "reservation": monkeypatch.setattr(repository, "reservations_of_group", lambda _: [{}])
    elif blocker == "reset": monkeypatch.setattr(repository, "decisions_of_group", lambda _: [{"recovery_reset_required": True}])
    elif blocker == "time": monkeypatch.setattr(operation, "busy", lambda _: True)
    elif blocker == "attempt": monkeypatch.setattr(run_store, "has_active_attempts", lambda _: True)
    elif blocker == "execution": monkeypatch.setattr(execution_store, "working", lambda _: True)
    elif blocker == "integration": monkeypatch.setattr(result_merge, "in_progress", lambda _: True)
    else: monkeypatch.setattr(cleanup._gs().db_git, "get_open_session_by_group", lambda _: {})
    assert call(env)["reason"] == reason
def test_legacy_still_checks_group_activity(env, monkeypatch):
    from flowgate.graph_time_machine import operation
    monkeypatch.setattr(operation, "busy", lambda _: True)
    assert call(env)["reason"] == "time_machine"
@pytest.mark.parametrize("method,suffix,permission", [
    ("get", "archive", "project.document.read"), ("post", "archive", "project.document.update"),
    ("post", "restore", "project.document.update"),
])
def test_project_scoped_permissions(env, method, suffix, permission):
    env["denied"].add(permission)
    assert call(env, method, suffix, 403)["error"]["code"] == "flow_access_denied"
    assert env["permissions"][-1] == ("reader", "archive", permission)
    assert not env["events"]
