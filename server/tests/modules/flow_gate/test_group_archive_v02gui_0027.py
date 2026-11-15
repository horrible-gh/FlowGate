"""flowgate.v02gui.0027 (0006-T) — display-only Group (module) archive / restore.

Real SQLite with every migration, the real FlowGateStore, the real module routes and the real tree
builders; only authentication and the SSE delivery are replaced. Covers the routes (archive,
restore, 409 / 404 / 422 / 401), the project_modules upsert of a groups.module-only Group, the tree
filter (default hides the Group with its Flows and documents, include_archived shows it marked
archived) and that nothing but the two archive columns changes.
"""
from __future__ import annotations

from pathlib import Path
import sqlite3

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

PROJECT = "garc"
ROW_FLOW = "garc.ui.0001"      # Flow of the Group that has a project_modules row ("ui")
LEGACY_FLOW = "garc.old.0001"  # Flow of the Group known only through groups.module ("old")
OTHER_FLOW = "garc.core.0001"  # Flow of a Group that is never archived ("core")


def sql(env, statement, params=()):
    with sqlite3.connect(env["db"]) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(row) for row in conn.execute(statement, params).fetchall()]


@pytest.fixture
def env(tmp_path, monkeypatch):
    from modules.flow_gate.api.v1 import module_routes
    from modules.flow_gate.db import _SqliteDbAdapter, _SqliteTxnAdapter, connection
    from modules.flow_gate.graph_workflow.services import canvas_events

    db = tmp_path / "group_archive.db"
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
    state = {"db": db, "events": []}

    def auth(request):
        if request.headers.get("authorization") == "Bearer user":
            return {"issued_to": "alice", "_is_user_jwt": True}
        return JSONResponse(status_code=401, content={"ok": False, "http_status": 401})

    monkeypatch.setattr(module_routes, "verify_bearer", auth)
    monkeypatch.setattr(canvas_events, "broadcast_event_threadsafe", lambda event: state["events"].append(event))
    app = FastAPI()
    app.include_router(module_routes.router)
    state["client"] = TestClient(app)

    now = "2026-01-01T00:00:00+00:00"
    sql(state, "INSERT INTO projects(project_id, project_name, is_active, created_at, updated_at) "
               "VALUES (?, 'Group archive', 1, ?, ?)", [PROJECT, now, now])
    sql(state, "INSERT INTO project_modules(module_id, project_id, name, title, created_at, updated_at) "
               "VALUES (?, ?, 'ui', 'UI 개선', ?, ?)", [f"{PROJECT}:ui", PROJECT, now, now])
    for flow, module in [(ROW_FLOW, "ui"), (LEGACY_FLOW, "old"), (OTHER_FLOW, "core")]:
        sql(state, "INSERT INTO groups(group_id, project_id, module, title, status, created_at, updated_at) "
                   "VALUES (?, ?, ?, ?, 'OPEN', ?, ?)", [flow, PROJECT, module, f"Flow {module}", now, now])
        sql(state, "INSERT INTO documents(doc_id, project_id, module, group_id, type_code, seq, title, status, "
                   "created_at, updated_at) VALUES (?, ?, ?, ?, 'R', 1, 'Root', 'open', ?, ?)",
            [flow + ".0001-R", PROJECT, module, flow, now, now])
    # A document of the "ui" Group whose Flow row is gone (an orphan under the module).
    sql(state, "INSERT INTO documents(doc_id, project_id, module, group_id, type_code, seq, title, status, "
               "created_at, updated_at) VALUES ('garc.ui.0009.0001-R', ?, 'ui', 'garc.ui.0009', 'R', 1, 'Lost', "
               "'open', ?, ?)", [PROJECT, now, now])
    return state


def post(env, module, action="archive", expected=200, token="user"):
    response = env["client"].post(f"/api/v1/projects/{PROJECT}/modules/{module}/{action}",
                                  headers={"Authorization": "Bearer " + token})
    assert response.status_code == expected, response.text
    return response.json()


def tree(include_archived=False, include_terminal=True):
    from modules.flow_gate.api.v1 import tree_routes
    return tree_routes.get_groups_tree(PROJECT, include_terminal=include_terminal,
                                       include_archived=include_archived, include_summary=False, force=True)["data"]["nodes"]


def ids(nodes):
    return {node["id"] for node in nodes}


def module_row(env, name):
    rows = sql(env, "SELECT * FROM project_modules WHERE project_id = ? AND name = ?", [PROJECT, name])
    return rows[0] if rows else None


def test_migration_adds_nullable_archive_columns(env):
    row = module_row(env, "ui")
    assert "archived_at" in row and "archived_by" in row
    assert row["archived_at"] is None and row["archived_by"] is None


def test_archive_hides_group_flows_and_documents_and_restore_brings_them_back(env):
    view = post(env, "ui")
    assert view["archived"] is True and view["archived_by"] == "alice" and view["archived_at"]
    assert view["name"] == "ui" and view["title"] == "UI 개선"
    row = module_row(env, "ui")
    assert row["archived_at"] == view["archived_at"] and row["archived_by"] == "alice"

    for nodes in (tree(), tree(include_terminal=False)):
        assert f"module:{PROJECT}:ui" not in ids(nodes)
        assert not any(node["id"].startswith("garc.ui.") for node in nodes)
        assert not any("orphan:" in node["id"] and ":ui:" in node["id"] for node in nodes)
        # The other Groups are untouched.
        assert {f"module:{PROJECT}:core", OTHER_FLOW, f"module:{PROJECT}:old", LEGACY_FLOW} <= ids(nodes)

    for nodes in (tree(include_archived=True), tree(include_archived=True, include_terminal=False)):
        node = next(n for n in nodes if n["id"] == f"module:{PROJECT}:ui")
        assert node["archived"] is True and node["archived_at"] == view["archived_at"] and node["archived_by"] == "alice"
        assert node["title"] == "UI 개선"
        assert ROW_FLOW in ids(nodes)
        # Groups that are not archived carry no archive keys (their node shape is unchanged).
        core = next(n for n in nodes if n["id"] == f"module:{PROJECT}:core")
        assert "archived" not in core and "archived_at" not in core

    restored = post(env, "ui", "restore")
    assert restored["archived"] is False and restored["archived_at"] is None and restored["archived_by"] is None
    for _ in range(2):  # a fresh read (F5) shows it again, not a client-side exception
        nodes = tree()
        assert {f"module:{PROJECT}:ui", ROW_FLOW, ROW_FLOW + ".0001-R"} <= ids(nodes)
        assert "archived" not in next(n for n in nodes if n["id"] == f"module:{PROJECT}:ui")


def test_group_known_only_through_groups_module_gets_its_row_on_archive(env):
    assert module_row(env, "old") is None
    view = post(env, "old")
    assert view["archived"] is True and view["module_id"] == f"{PROJECT}:old"
    row = module_row(env, "old")
    assert row["title"] == "old" and row["archived_by"] == "alice" and row["archived_at"]
    assert LEGACY_FLOW not in ids(tree())
    assert next(n for n in tree(include_archived=True) if n["id"] == f"module:{PROJECT}:old")["archived"] is True

    post(env, "old", "restore")
    row = module_row(env, "old")
    assert row is not None and row["archived_at"] is None and row["archived_by"] is None
    nodes = tree()
    assert LEGACY_FLOW in ids(nodes)
    assert next(n for n in nodes if n["id"] == f"module:{PROJECT}:old")["title"] == "old"


def test_refusals(env):
    assert post(env, "ui", "restore", 409)["code"] == "not_archived"
    assert post(env, "old", "restore", 409)["code"] == "not_archived"
    assert module_row(env, "old") is None  # a refused restore writes nothing
    post(env, "ui")
    assert post(env, "ui", expected=409)["code"] == "already_archived"
    assert post(env, "missing", expected=404)["code"] == "module_not_found"
    assert post(env, "none", expected=404)["code"] == "module_not_found"
    post(env, "Bad.Name", expected=422)
    post(env, "core", expected=401, token="nobody")
    assert module_row(env, "core") is None
    assert module_row(env, "ui")["archived_by"] == "alice"


def test_events_are_project_wide_group_view_refresh(env):
    post(env, "ui")
    post(env, "ui", "restore")
    post(env, "ui", "restore", 409)
    assert [(e.event_type, e.payload["reason"], e.payload["module"], e.project, e.group_id, e.audience)
            for e in env["events"]] == [
        ("group_view_refresh", "module_archived", "ui", PROJECT, None, "*"),
        ("group_view_refresh", "module_restored", "ui", PROJECT, None, "*"),
    ]
    assert all(e.payload["group_id"] is None and e.payload["actor_user_id"] == "alice" for e in env["events"])


def test_roundtrip_changes_nothing_but_the_archive_columns(env):
    sql(env, "INSERT INTO graph_runs(run_id,group_id,revision_no,status,active_group_id,started_at,"
             "policy_source,policy_set_id,policy_set_version,policy_digest,updated_at,idle_reason) "
             "VALUES ('r1', ?, 1, 'running', ?, '2026-01-02', 'flow', 'test', '1', ?, '2026-01-02', NULL)",
        [ROW_FLOW, ROW_FLOW, "a" * 64])
    # The Flow archive is independent: a Flow archived on its own stays archived across the Group round trip.
    sql(env, "UPDATE groups SET archived_at = '2026-01-03', archived_by = 'bob' WHERE group_id = ?", [OTHER_FLOW])
    sql(env, "UPDATE groups SET module = 'ui' WHERE group_id = ?", [OTHER_FLOW])
    tables = ["groups", "documents", "graph_runs", "events"]
    before = {table: sql(env, f"SELECT * FROM {table} ORDER BY 1") for table in tables}
    module_before = module_row(env, "ui")

    post(env, "ui")  # a running Flow does not block the (display-only) Group archive
    post(env, "ui", "restore")

    assert {table: sql(env, f"SELECT * FROM {table} ORDER BY 1") for table in tables} == before
    assert module_row(env, "ui") == module_before
    flow = next(n for n in tree(include_archived=True) if n["id"] == OTHER_FLOW)
    assert flow["archived_at"] == "2026-01-03"
    assert OTHER_FLOW not in ids(tree())


def test_title_edit_keeps_the_archive_and_hidden_group_stays_editable(env):
    post(env, "ui")
    response = env["client"].patch(f"/api/v1/projects/{PROJECT}/modules/ui", json={"title": "UI 정리"},
                                   headers={"Authorization": "Bearer user"})
    assert response.status_code == 200, response.text
    row = module_row(env, "ui")
    assert row["title"] == "UI 정리" and row["archived_by"] == "alice" and row["archived_at"]
    node = next(n for n in tree(include_archived=True) if n["id"] == f"module:{PROJECT}:ui")
    assert node["title"] == "UI 정리" and node["archived"] is True
