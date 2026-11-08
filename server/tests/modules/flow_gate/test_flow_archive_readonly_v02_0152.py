"""0.2 archive viewing: API writes refuse, reads survive, and restore releases the lock."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import Mock

import pytest
from test_flow_archive_v02test_0002 import env, seed_run, seed_decision, sql, FLOW, call

AUTH = {"Authorization": "Bearer user"}
DOC = FLOW + ".0001-R"


def archived(env):
    seed_run(env)
    seed_decision(env)
    call(env, "post")


def test_graph_and_card_commands_refuse_without_changing_stored_state(env, monkeypatch):
    from modules.flow_gate.graph_workflow.api import canvas_routes, run_routes
    from flowgate.card_settings import routes as settings_routes
    from flowgate.card_memos import routes as memo_routes
    from flowgate.card_memos import legacy_adapter as memo_legacy
    app = env["client"].app
    for router in [canvas_routes.router, run_routes.router, settings_routes.router, memo_routes.router]:
        app.include_router(router)
    monkeypatch.setattr(memo_legacy, "has_permission", lambda *args: True)
    from modules.flow_gate.services import graph_execution_hooks
    def execution_read(name, *args):
        assert name in ("execution_wire", "held_nodes"), name
        return {} if name == "execution_wire" else []
    monkeypatch.setattr(graph_execution_hooks, "call", execution_read)
    archived(env)
    tables = ["documents", "graph_runs", "graph_node_attempts", "gate_decisions", "groups"]
    before = {table: sql(env, f"SELECT * FROM {table}") for table in tables}
    prefix = f"/api/v1/flows/{FLOW}"
    commands = [
        ("put", prefix + "/graph/draft", {"draft_version": 0, "nodes": [], "edges": []}),
        ("post", prefix + "/graph/revisions", {"draft_version": 1}),
        ("put", prefix + "/graph/nodes/n/title", {"title": "changed"}),
        ("put", prefix + "/canvas/layout", {"positions": [], "viewport": {"x": 2, "y": 3, "zoom": 1}}),
        ("post", prefix + "/runs", {"mode": "full"}),
        ("post", "/api/v1/runs/current/pause", {}),
        ("post", "/api/v1/runs/current/resume", {}),
        ("post", "/api/v1/runs/current/stop", {}),
        ("post", "/api/v1/runs/current/nodes/gate/gate-decision", {"decision": "confirm"}),
        ("patch", prefix + "/nodes/n/settings", {"field": "hold", "value": True}),
        ("post", prefix + "/nodes/n/memos", {"title": "memo", "body": "changed"}),
        ("patch", prefix + "/nodes/n/memos/m", {"body": "changed"}),
        ("delete", prefix + "/nodes/n/memos/m", None),
    ]
    for method, path, body in commands:
        response = env["client"].request(method, path, headers=AUTH, **({"json": body} if body is not None else {}))
        assert response.status_code == 409, (path, response.text)
        assert response.json()["error"]["code"] == "flow_archived", (path, response.text)
    assert {table: sql(env, f"SELECT * FROM {table}") for table in tables} == before
    for path in ["/graph", "/canvas", "/runs/current", "/archive"]:
        response = env["client"].get(prefix + path, headers=AUTH)
        assert response.status_code == 200, (path, response.text)
    assert {table: sql(env, f"SELECT * FROM {table}") for table in tables} == before
    for spy in env["forbidden"]:
        spy.assert_not_called()


def test_archive_wins_after_write_preflight_before_guard(env, monkeypatch):
    from modules.flow_gate.db import groups
    from modules.flow_gate.graph_workflow.domain.layout_request import parse_layout
    from modules.flow_gate.graph_workflow.services import layout_service, flow_guard
    from modules.flow_gate.graph_workflow.services.errors import GraphApiError
    # Hold the same real DB/process guard used by archive while a layout write passes preflight.
    held, waiting, release = Event(), Event(), Event()
    monkeypatch.setattr(layout_service, "ensure_identity", lambda *args: None)
    original = layout_service.with_flow_guard
    def delayed(flow_id, callback):
        waiting.set()
        return original(flow_id, callback)
    monkeypatch.setattr(layout_service, "with_flow_guard", delayed)
    write_viewport = Mock()
    monkeypatch.setattr(layout_service.layout_store, "save_viewport", write_viewport)
    def archive():
        def locked():
            held.set()
            assert release.wait(10)
            groups.set_archive(FLOW, True, "reader")
        flow_guard.with_flow_guard(FLOW, locked)
    with ThreadPoolExecutor(max_workers=2) as pool:
        archive_future = pool.submit(archive)
        assert held.wait(10)
        write_future = pool.submit(layout_service.save_layout, groups.get_by_id(FLOW), "reader",
                                   parse_layout({"viewport": {"x": 1, "y": 2, "zoom": 1}}), None)
        try:
            assert waiting.wait(10)
        finally:
            release.set()
        archive_future.result(timeout=10)
        with pytest.raises(GraphApiError) as error:
            write_future.result(timeout=10)
        assert error.value.code == "flow_archived"
    write_viewport.assert_not_called()


def test_restore_allows_existing_layout_and_group_edit_again(env):
    from modules.flow_gate import process_service
    from modules.flow_gate.graph_workflow.api import canvas_routes
    from modules.flow_gate.graph_workflow.services.errors import GraphApiError
    env["client"].app.include_router(canvas_routes.router)
    archived(env)
    with pytest.raises(GraphApiError, match="flow_archived"):
        process_service.update_group(FLOW, title="blocked")
    call(env, "post", "restore")
    sql(env, "INSERT INTO users (user_id, username, email, password, created_at, updated_at) "
             "VALUES (\'reader\', \'reader\', \'reader@example.test\', \'x\', \'t\', \'t\')")
    process_service.update_group(FLOW, title="restored")
    assert sql(env, "SELECT title FROM groups WHERE group_id=?", [FLOW])[0]["title"] == "restored"
    response = env["client"].put(f"/api/v1/flows/{FLOW}/canvas/layout", headers=AUTH,
                                json={"viewport": {"x": 4, "y": 5, "zoom": 1.2}})
    assert response.status_code == 200, response.text
    assert response.json()["viewport_saved"] is True


def test_archived_flow_tree_commands_refuse_before_git_or_disposal(env, monkeypatch):
    from modules.flow_gate.api.v1 import tree_routes
    from modules.flow_gate.workflow.routers import workflow
    from modules.flow_gate.services.git.group_work_base import change_group_work_base_ref
    from modules.flow_gate.services.git.credentials import GitServiceError
    from fastapi import HTTPException
    archived(env)
    app = env["client"].app
    app.include_router(tree_routes.router)
    response = env["client"].post(f"/api/v1/groups/{FLOW}/dispose", headers=AUTH,
                                 json={"reason_option": "other", "reason_detail": "blocked"})
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "flow_archived"
    monkeypatch.setattr(workflow, "_get_user_permissions", lambda user: {"project.group.manage"})
    with pytest.raises(HTTPException) as error:
        workflow.update_group_endpoint(FLOW, workflow.GroupUpdateRequest(title="blocked"), {"user_id": "reader"})
    assert error.value.detail["code"] == "flow_archived"
    with pytest.raises(GitServiceError) as error:
        change_group_work_base_ref(FLOW, "main")
    assert error.value.code == "flow_archived"
    for spy in env["forbidden"]:
        spy.assert_not_called()


def test_document_attachment_qna_and_source_writes_are_locked(env):
    from modules.flow_gate.db import documents
    from modules.flow_gate.documents.routers.documents import _reject_if_group_disposed
    from modules.flow_gate.documents.attachments.service import assert_mutable
    from modules.flow_gate.documents.attachments.errors import AttachmentError
    from modules.flow_gate.api.v1 import q_tapi_routes as q
    from modules.flow_gate.services import graph_source_hooks
    from fastapi import HTTPException
    archived(env)
    doc = documents.get_by_id(DOC)
    with pytest.raises(HTTPException) as error:
        _reject_if_group_disposed(doc)
    assert error.value.status_code == 409 and error.value.detail["code"] == "flow_archived"
    with pytest.raises(AttachmentError) as error:
        assert_mutable(doc, None, "upload")
    assert error.value.details["reason"] == "flow_archived"
    body = q.AddQuestionsRequest(questions=[{"title": "Question", "body": "Still mutable?"}])
    response = q._add_questions_response(DOC, body, "reader")
    assert response.status_code == 409 and b"flow_archived" in response.body
    assert sql(env, "SELECT * FROM questions") == []
    with pytest.raises(graph_source_hooks.GraphSourceError) as error:
        graph_source_hooks.require_writable({"group_id": FLOW}, "write")
    assert error.value.code == "flow_archived"
    call(env, "post", "restore")
    graph_source_hooks.require_writable({"group_id": FLOW}, "write")
