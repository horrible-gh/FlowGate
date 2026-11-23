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
    ("paused", None), ("idle", "waiting_confirm"), ("idle", "needs_intervention"),
    ("idle", "gate_publish"), ("idle", "hold"), ("idle", "step_completed"), ("idle", None),
])
def test_incomplete_run_can_archive_without_activity(env, status, reason):
    seed_run(env, status=status, reason=reason)
    seed_decision(env)
    view = call(env)
    assert view["eligible"] is True and view["reason"] is None
    assert view["completed"] is False
    archived = call(env, "post")
    assert archived["archived"] is True and archived["completed"] is False
    assert sql(env, "SELECT archived_at FROM groups WHERE group_id=?", [FLOW])[0]["archived_at"] is not None


def test_running_without_active_work_still_refuses_archive(env):
    seed_run(env, status="running", reason=None)
    seed_decision(env)
    view = call(env)
    assert view["eligible"] is False and view["reason"] == "work_unfinished"
    assert view["completed"] is False
    assert call(env, "post", expected=409)["error"]["code"] == "work_unfinished"
    assert sql(env, "SELECT archived_at FROM groups WHERE group_id=?", [FLOW])[0]["archived_at"] is None


@pytest.mark.parametrize("status", ["accepted", "failed", "recovery_required"])
def test_any_unfinished_gate_in_current_run_refuses_multi_gate_archive(env, status):
    seed_run(env)
    seed_decision(env)
    seed_decision(env, status=status, ident="other-gate")
    assert call(env)["reason"] == "gate_job_unfinished"
