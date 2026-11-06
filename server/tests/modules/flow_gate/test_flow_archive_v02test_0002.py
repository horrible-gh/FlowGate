"""T-c: archive is display-only, current-run scoped, guarded and reversible."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3
import subprocess
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi.responses import JSONResponse

FLOW = "archive.ui.0001"


@pytest.fixture
def env(tmp_path, monkeypatch):
    from modules.flow_gate.db import connection, _SqliteDbAdapter, _SqliteTxnAdapter
    from modules.flow_gate.graph_workflow.api import envelope, graph_routes
    from modules.flow_gate.graph_workflow.services import canvas_events
    from modules.flow_gate.services import git_service
    from flowgate.gate_decisions import cleanup
    from modules.flow_gate import process_service
    db = tmp_path / "archive.db"
    conn = sqlite3.connect(db)
    migrations = Path(__file__).resolve().parents[3] / "sql/migrations/sqlite"
    for path in sorted(migrations.glob("*.sql")):
        try:
            conn.executescript(path.read_text(encoding="utf-8"))
        except sqlite3.OperationalError:
            if path.name.startswith("170_"):
                raise
    conn.commit()
    conn.close()
    store = connection.FlowGateStore()
    store._db = _SqliteDbAdapter(str(db))
    monkeypatch.setattr(connection, "STORE", store)
    monkeypatch.setattr(_SqliteTxnAdapter, "cursor", property(lambda self: self._cur), raising=False)
    monkeypatch.setenv("FLOWGATE_STORAGE_DIR", str(tmp_path / "storage"))
    state = {"db": db, "denied": set(), "events": [], "permissions": [], "store": store}
    def auth(request):
        token = request.headers.get("authorization", "")
        if token == "Bearer worker":
            return {"issued_to": "reader"}
        if token == "Bearer user":
            return {"_is_user_jwt": True, "issued_to": "reader"}
        return JSONResponse(status_code=401, content={"ok": False})
    def perm(user, project, permission):
        state["permissions"].append((user, project, permission))
        return permission not in state["denied"]
    monkeypatch.setattr(envelope, "verify_bearer", auth)
    monkeypatch.setattr(envelope, "has_permission", perm)
    monkeypatch.setattr(canvas_events, "broadcast_event_threadsafe", lambda event: state["events"].append(event))
    forbidden = []
    for module, name in [(git_service, "_run_git"), (cleanup, "run"), (cleanup, "request"),
                         (process_service, "dispose_group")]:
        spy = Mock(side_effect=AssertionError("archive must not call " + name))
        monkeypatch.setattr(module, name, spy)
        forbidden.append(spy)
    state["forbidden"] = forbidden
    app = FastAPI()
    app.include_router(graph_routes.router)
    state["client"] = TestClient(app)
    sql(state, "INSERT INTO projects(project_id, project_name, is_active, created_at, updated_at) "
               "VALUES ('archive', 'Archive test', 1, '2026-01-01', '2026-01-01')")
    sql(state, "INSERT INTO groups(group_id, project_id, module, title, status, created_at, updated_at) "
               "VALUES (?, 'archive', 'ui', 'Completed', 'OPEN', '2026-01-01', '2026-01-01')", [FLOW])
    sql(state, "INSERT INTO documents(doc_id, project_id, module, group_id, type_code, seq, title, status, "
               "doc_review_status, created_at, updated_at) VALUES (?, 'archive', 'ui', ?, 'R', 1, 'Root', "
               "'open', 'wf_done', '2026-01-01', '2026-01-01')", [FLOW + '.0001-R', FLOW])
    return state


def sql(env, statement, params=()):
    with sqlite3.connect(env["db"]) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(row) for row in conn.execute(statement, params).fetchall()]


def seed_run(env, run_id="current", status="idle", reason="completed", active=True):
    if active:
        sql(env, "UPDATE graph_runs SET active_group_id=NULL WHERE group_id=?", [FLOW])
    sql(env, "INSERT INTO graph_runs(run_id,group_id,revision_no,status,active_group_id,started_at,"
             "policy_source,policy_set_id,policy_set_version,policy_digest,updated_at,idle_reason) "
             "VALUES (?, ?, 1, ?, ?, '2026-01-02', 'flow', 'test', '1', ?, '2026-01-02', ?)",
        [run_id, FLOW, status, FLOW if active else None, "a" * 64, reason])


def seed_decision(env, run_id="current", status="succeeded", ident="decision"):
    sql(env, "INSERT INTO gate_decisions(decision_id,group_id,run_id,node_key,attempt_id,valid_attempt_key,"
             "revision_no,actor_user_id,request_id,request_fingerprint,decision,status,accepted_at,updated_at) "
             "VALUES (?, ?, ?, 'gate', ?, ?, 1, 'reader', ?, ?, 'confirm', ?, '2026-01-02', '2026-01-02')",
        [ident, FLOW, run_id, ident, ident, ident, "b" * 64, status])


def call(env, method="get", suffix="archive", expected=200, token="user"):
    response = getattr(env["client"], method)(f"/api/v1/flows/{FLOW}/{suffix}", headers={"Authorization": "Bearer " + token})
    assert response.status_code == expected, response.text
    return response.json()


def test_completed_current_run_qualifies(env):
    seed_run(env)
    seed_decision(env)
    view = call(env)
    assert view["eligible"] and view["reason"] is None and not view["archived"]
    assert env["permissions"] == [("reader", "archive", "perm_document_read")]


@pytest.mark.parametrize("status,reason", [
    ("running", None), ("paused", None), ("idle", "waiting_confirm"), ("idle", "needs_intervention"),
    ("idle", "gate_publish"), ("idle", "hold"), ("idle", "step_completed"), ("idle", None),
])
def test_incomplete_run_refused_even_with_successful_intermediate_gate(env, status, reason):
    seed_run(env, status=status, reason=reason)
    seed_decision(env)
    assert call(env)["reason"] == "run_not_completed"
    assert call(env, "post", expected=409)["error"]["code"] == "run_not_completed"
    assert sql(env, "SELECT archived_at FROM groups WHERE group_id=?", [FLOW])[0]["archived_at"] is None


@pytest.mark.parametrize("status", ["accepted", "failed", "recovery_required"])
def test_any_unfinished_gate_in_current_run_refuses_multi_gate_archive(env, status):
    seed_run(env)
    seed_decision(env)
    seed_decision(env, status=status, ident="other-gate")
    assert call(env)["reason"] == "gate_job_unfinished"


def test_old_run_success_cannot_qualify_current_run(env):
    seed_run(env, "old", active=False)
    seed_decision(env, "old")
    seed_run(env)
    assert call(env)["reason"] == "no_gate_decision"
    sql(env, "UPDATE graph_runs SET status='running' WHERE run_id='current'")
    assert call(env)["reason"] == "run_not_completed"


def test_current_run_missing_with_any_history_is_not_legacy(env):
    seed_run(env, status="idle", active=False)
    assert call(env)["reason"] == "run_not_completed"


def test_legacy_live_root_only(env):
    assert call(env)["eligible"] is True
    sql(env, "UPDATE documents SET superseded_by='successor'")
    assert call(env)["eligible"] is False


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
    ("get", "archive", "perm_document_read"), ("post", "archive", "perm_document_update"),
    ("post", "restore", "perm_document_update"),
])
def test_project_scoped_permissions(env, method, suffix, permission):
    env["denied"].add(permission)
    assert call(env, method, suffix, 403)["error"]["code"] == "flow_access_denied"
    assert env["permissions"][-1] == ("reader", "archive", permission)
    assert not env["events"]


def test_worker_token_is_not_a_user(env):
    assert call(env, "post", expected=403, token="worker")["error"]["code"] == "token_kind_not_allowed"


def test_archive_rechecks_after_get_and_duplicate_requests_are_idempotent(env):
    seed_run(env)
    seed_decision(env)
    assert call(env)["eligible"]
    sql(env, "UPDATE graph_runs SET status='running' WHERE run_id='current'")
    call(env, "post", expected=409)
    sql(env, "UPDATE graph_runs SET status='idle' WHERE run_id='current'")
    first = call(env, "post")
    second = call(env, "post")
    assert first["archived_at"] == second["archived_at"]
    assert first["archived_by"] == "reader"
    assert len(env["events"]) == 1
    assert call(env, "post", "restore")["archived"] is False
    call(env, "post", "restore")
    assert len(env["events"]) == 2


def test_concurrent_archive_updates_once(env):
    from modules.flow_gate.graph_workflow.services import flow_archive
    def archive(_):
        return flow_archive.change(FLOW, "reader", True, lambda *args: None)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(archive, range(2)))
    assert all(result["archived"] for result in results)
    assert results[0]["archived_at"] == results[1]["archived_at"]
    assert len(env["events"]) == 1


def test_roundtrip_preserves_documents_runs_decisions_git_and_status(env, tmp_path):
    seed_run(env)
    seed_decision(env)
    tables = ["documents", "graph_runs", "graph_node_attempts", "gate_decisions", "gate_decision_effects",
              "gate_archive_checkpoints", "gate_cleanup_records"]
    before = {table: sql(env, f"SELECT * FROM {table}") for table in tables}
    group_before = sql(env, "SELECT status,deleted_at,updated_at,closed_at,work_base_ref FROM groups")[0]
    repo = tmp_path / "repo"
    repo.mkdir()
    def git(*args):
        return subprocess.check_output(["git", *args], cwd=repo, text=True).strip()
    git("init", "-q")
    git("-c", "user.name=Archive Test", "-c", "user.email=archive@example.test", "commit", "--allow-empty", "-qm", "fixture")
    git("update-ref", "refs/flowgate/archive/test", "HEAD")
    refs = git("show-ref")
    call(env, "post")
    call(env, "post", "restore")
    assert git("show-ref") == refs
    assert {table: sql(env, f"SELECT * FROM {table}") for table in tables} == before
    assert sql(env, "SELECT status,deleted_at,updated_at,closed_at,work_base_ref FROM groups")[0] == group_before
    for spy in env["forbidden"]: spy.assert_not_called()


def test_tree_hides_whole_archive_and_restored_terminal_flow_stays_visible(env):
    from modules.flow_gate import process_service
    from modules.flow_gate.api.v1 import tree_routes
    call(env, "post")
    for tree in [process_service.get_group_tree("archive"), process_service.get_hidden_group_tree("archive")]:
        assert not any(FLOW in node["id"] for node in tree["nodes"])
    shown = tree_routes.get_groups_tree("archive", include_terminal=False, include_archived=True, include_summary=False)
    group = next(n for n in shown["data"]["nodes"] if n["id"] == FLOW)
    assert group["archived_at"] and group["is_final_approved"]
    assert any(n["id"] == FLOW + '.0001-R' for n in shown["data"]["nodes"])
    call(env, "post", "restore")
    for _ in range(2):  # a fresh read/F5, not a local UI exception
        visible = process_service.get_hidden_group_tree("archive")["nodes"]
        assert any(n["id"] == FLOW and n["is_final_approved"] for n in visible)
    sql(env, "UPDATE documents SET type_code='DC'")
    assert not any(n["id"] == FLOW for n in process_service.get_hidden_group_tree("archive")["nodes"])


def test_conditional_db_writes_and_migration_defaults(env):
    from modules.flow_gate.db import groups
    initial = groups.get_by_id(FLOW)
    assert all(initial[name] is None for name in ("archived_at", "archived_by", "archive_restored_at"))
    assert groups.set_archive(FLOW, True, "first")
    assert not groups.set_archive(FLOW, True, "second")
    assert groups.get_by_id(FLOW)["archived_by"] == "first"
    assert groups.set_archive(FLOW, False, "reader")
    assert not groups.set_archive(FLOW, False, "reader")
