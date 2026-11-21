"""delete an empty Group (module) only.

Real SQLite with every migration, the real FlowGateStore, the real module routes and the real tree
builders; only authentication and the SSE delivery are replaced. Covers the route (200 / 409
``module_not_empty`` with ``flow_count`` / 404 / 422 / 401), the "empty" rule (archived and
discarded Flows count, soft-deleted ones do not), the tree's ``flow_total`` (independent of
``include_archived``), the conditional delete, the refresh event and that nothing but the Group's
``project_modules`` row is removed.
"""
from __future__ import annotations

from pathlib import Path
import sqlite3

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

PROJECT = "gdel"
NOW = "2026-01-01T00:00:00+00:00"


def sql(env, statement, params=()):
    with sqlite3.connect(env["db"]) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(row) for row in conn.execute(statement, params).fetchall()]


def add_module(env, name, title=None):
    sql(env, "INSERT INTO project_modules(module_id, project_id, name, title, created_at, updated_at) "
             "VALUES (?, ?, ?, ?, ?, ?)", [f"{PROJECT}:{name}", PROJECT, name, title or name, NOW, NOW])


def add_flow(env, flow_id, module, **extra):
    sql(env, "INSERT INTO groups(group_id, project_id, module, title, status, created_at, updated_at) "
             "VALUES (?, ?, ?, ?, 'OPEN', ?, ?)", [flow_id, PROJECT, module, f"Flow {flow_id}", NOW, NOW])
    for column, value in extra.items():
        sql(env, f"UPDATE groups SET {column} = ? WHERE group_id = ?", [value, flow_id])
    sql(env, "INSERT INTO documents(doc_id, project_id, module, group_id, type_code, seq, title, status, "
             "created_at, updated_at) VALUES (?, ?, ?, ?, 'R', 1, 'Root', 'open', ?, ?)",
        [flow_id + ".0001-R", PROJECT, module, flow_id, NOW, NOW])


@pytest.fixture
def env(tmp_path, monkeypatch):
    from modules.flow_gate.api.v1 import module_routes
    from modules.flow_gate.db import _SqliteDbAdapter, _SqliteTxnAdapter, connection
    from modules.flow_gate.graph_workflow.services import canvas_events

    db = tmp_path / "group_delete.db"
    conn = sqlite3.connect(db)
    migrations = Path(__file__).resolve().parents[3] / "sql/migrations/sqlite"
    for path in sorted(migrations.glob("*.sql")):
        try:
            conn.executescript(path.read_text(encoding="utf-8"))
        except sqlite3.OperationalError:
            if path.name.startswith("190_"):
                raise
    conn.commit()
    conn.close()
    store = connection.FlowGateStore()
    store._db = _SqliteDbAdapter(str(db))
    monkeypatch.setattr(connection, "STORE", store)
    monkeypatch.setattr(_SqliteTxnAdapter, "cursor", property(lambda self: self._cur), raising=False)
    monkeypatch.setenv("FLOWGATE_STORAGE_DIR", str(tmp_path / "storage"))
    state = {"db": db, "events": [], "store": store}

    def auth(request):
        if request.headers.get("authorization") == "Bearer user":
            return {"issued_to": "alice", "_is_user_jwt": True}
        return JSONResponse(status_code=401, content={"ok": False, "http_status": 401})

    monkeypatch.setattr(module_routes, "verify_bearer", auth)
    monkeypatch.setattr(canvas_events, "broadcast_event_threadsafe", lambda event: state["events"].append(event))
    app = FastAPI()
    app.include_router(module_routes.router)
    state["client"] = TestClient(app)

    sql(state, "INSERT INTO projects(project_id, project_name, is_active, created_at, updated_at) "
               "VALUES (?, 'Group delete', 1, ?, ?)", [PROJECT, NOW, NOW])
    add_module(state, "empty", "빈 그룹")          # project_modules row, no Flow
    add_module(state, "busy", "Flow 있는 그룹")     # project_modules row with live / archived / discarded Flows
    add_flow(state, f"{PROJECT}.busy.0001", "busy")
    add_flow(state, f"{PROJECT}.busy.0002", "busy", archived_at="2026-01-02", archived_by="bob")
    add_flow(state, f"{PROJECT}.busy.0003", "busy")
    sql(state, "INSERT INTO documents(doc_id, project_id, module, group_id, type_code, seq, title, status, "
               "created_at, updated_at) VALUES (?, ?, 'busy', ?, 'DC', 2, 'Discard', 'open', ?, ?)",
        [f"{PROJECT}.busy.0003.0002-DC", PROJECT, f"{PROJECT}.busy.0003", NOW, NOW])
    add_flow(state, f"{PROJECT}.legacy.0001", "legacy")  # Group known only through groups.module
    return state


def delete(env, module, expected=200, token="user"):
    response = env["client"].delete(f"/api/v1/projects/{PROJECT}/modules/{module}",
                                    headers={"Authorization": "Bearer " + token})
    assert response.status_code == expected, response.text
    return response.json()


def tree(include_archived=False, include_terminal=True):
    from modules.flow_gate.api.v1 import tree_routes
    return tree_routes.get_groups_tree(PROJECT, include_terminal=include_terminal,
                                       include_archived=include_archived, include_summary=False, force=True)["data"]["nodes"]


def module_node(nodes, name):
    return next((n for n in nodes if n["id"] == f"module:{PROJECT}:{name}"), None)


def module_row(env, name):
    rows = sql(env, "SELECT * FROM project_modules WHERE project_id = ? AND name = ?", [PROJECT, name])
    return rows[0] if rows else None


def test_tree_flow_total_counts_every_flow_whatever_the_archive_toggle(env):
    for include_archived in (False, True):
        for include_terminal in (True, False):
            nodes = tree(include_archived=include_archived, include_terminal=include_terminal)
            assert module_node(nodes, "empty")["flow_total"] == 0
            # live + archived + discarded: 3, even when the archived Flow is not in this tree
            assert module_node(nodes, "busy")["flow_total"] == 3
            assert module_node(nodes, "legacy")["flow_total"] == 1
    default_ids = {n["id"] for n in tree()}
    assert f"{PROJECT}.busy.0002" not in default_ids  # the archived Flow is hidden, yet counted above


def test_empty_group_is_deleted_and_leaves_the_tree(env):
    body = delete(env, "empty")
    assert body == {"ok": True, "deleted": True, "module_id": f"{PROJECT}:empty",
                    "project_id": PROJECT, "name": "empty"}
    assert module_row(env, "empty") is None
    for _ in range(2):  # a fresh read (F5) does not bring it back
        for include_archived in (False, True):
            assert module_node(tree(include_archived=include_archived), "empty") is None
    assert [(e.event_type, e.payload["reason"], e.payload["module"], e.payload["group_id"],
             e.payload["actor_user_id"], e.project, e.group_id, e.audience) for e in env["events"]] == [
        ("group_view_refresh", "module_deleted", "empty", None, "alice", PROJECT, None, "*"),
    ]
    assert delete(env, "empty", 404)["code"] == "module_not_found"


def test_group_with_flows_is_refused_with_the_flow_count(env):
    body = delete(env, "busy", 409)
    assert body["code"] == "module_not_empty" and body["flow_count"] == 3
    assert module_row(env, "busy")["title"] == "Flow 있는 그룹"
    assert module_node(tree(), "busy")["flow_total"] == body["flow_count"]
    # A Group known only through groups.module has Flows by definition: refused, not "not found".
    body = delete(env, "legacy", 409)
    assert body["code"] == "module_not_empty" and body["flow_count"] == 1
    assert env["events"] == []


def test_archived_flow_alone_and_discarded_flow_alone_keep_the_group(env):
    add_module(env, "arch")
    add_flow(env, f"{PROJECT}.arch.0001", "arch", archived_at="2026-01-02", archived_by="bob")
    assert delete(env, "arch", 409)["flow_count"] == 1
    add_module(env, "disc")
    add_flow(env, f"{PROJECT}.disc.0001", "disc")
    sql(env, "INSERT INTO documents(doc_id, project_id, module, group_id, type_code, seq, title, status, "
             "created_at, updated_at) VALUES (?, ?, 'disc', ?, 'DC', 2, 'Discard', 'open', ?, ?)",
        [f"{PROJECT}.disc.0001.0002-DC", PROJECT, f"{PROJECT}.disc.0001", NOW, NOW])
    assert delete(env, "disc", 409)["flow_count"] == 1


def test_archived_empty_group_follows_the_same_rule(env):
    sql(env, "UPDATE project_modules SET archived_at = '2026-01-05', archived_by = 'bob' WHERE name = 'empty'")
    assert module_node(tree(), "empty") is None
    assert module_node(tree(include_archived=True), "empty")["flow_total"] == 0
    delete(env, "empty")
    assert module_row(env, "empty") is None
    assert module_node(tree(include_archived=True), "empty") is None


def test_soft_deleted_flow_rows_do_not_count_and_stay(env):
    add_module(env, "gone")
    add_flow(env, f"{PROJECT}.gone.0001", "gone", deleted_at="2026-01-03")
    assert module_node(tree(), "gone")["flow_total"] == 0
    delete(env, "gone")
    assert sql(env, "SELECT deleted_at FROM groups WHERE group_id = ?", [f"{PROJECT}.gone.0001"]) == [
        {"deleted_at": "2026-01-03"}]


def test_refusals(env):
    assert delete(env, "missing", 404)["code"] == "module_not_found"
    assert delete(env, "none", 404)["code"] == "module_not_found"
    delete(env, "Bad.Name", 422)
    delete(env, "empty", 401, token="nobody")
    assert module_row(env, "empty") is not None
    assert env["events"] == []


def test_delete_is_conditional_on_emptiness_at_write_time(env, monkeypatch):
    """A Flow created after the Group row was read still keeps the Group (check + delete are one statement)."""
    store = env["store"]
    original = store._fetch_one

    def fetch_then_flow_appears(statement, params=None):
        row = original(statement, params)
        if statement.startswith("SELECT * FROM project_modules") and params and params[1] == "empty":
            store._execute("INSERT INTO groups(group_id, project_id, module, title, status, created_at, updated_at) "
                           "VALUES (?, ?, 'empty', 'Late', 'OPEN', ?, ?)", [f"{PROJECT}.empty.0001", PROJECT, NOW, NOW])
        return row

    monkeypatch.setattr(store, "_fetch_one", fetch_then_flow_appears)
    body = delete(env, "empty", 409)
    assert body["code"] == "module_not_empty" and body["flow_count"] == 1
    monkeypatch.setattr(store, "_fetch_one", original)
    assert module_row(env, "empty") is not None
    assert env["events"] == []


def test_delete_changes_nothing_but_the_project_modules_row(env):
    sql(env, "INSERT INTO graph_runs(run_id,group_id,revision_no,status,active_group_id,started_at,"
             "policy_source,policy_set_id,policy_set_version,policy_digest,updated_at,idle_reason) "
             "VALUES ('r1', ?, 1, 'running', ?, '2026-01-02', 'flow', 'test', '1', ?, '2026-01-02', NULL)",
        [f"{PROJECT}.busy.0001", f"{PROJECT}.busy.0001", "a" * 64])
    # A soft-deleted Flow of the empty Group and a document left under its module name stay as they are.
    add_flow(env, f"{PROJECT}.empty.0001", "empty", deleted_at="2026-01-03")
    tables = ["groups", "documents", "graph_runs", "graph_run_events", "events"]
    before = {table: sql(env, f"SELECT * FROM {table} ORDER BY 1") for table in tables}
    modules_before = sql(env, "SELECT * FROM project_modules ORDER BY 1")

    delete(env, "busy", 409)
    delete(env, "empty")

    assert {table: sql(env, f"SELECT * FROM {table} ORDER BY 1") for table in tables} == before
    assert sql(env, "SELECT * FROM project_modules ORDER BY 1") == [
        row for row in modules_before if row["name"] != "empty"]
