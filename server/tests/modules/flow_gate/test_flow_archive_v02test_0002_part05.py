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

from starlette.requests import Request


def _tree_request():
    return Request({"type": "http", "headers": []})


@pytest.fixture(autouse=True)
def _authorized_tree_reader(monkeypatch):
    # These tests cover tree contents; authentication has its own route tests.
    from modules.flow_gate.api.v1 import tree_routes
    monkeypatch.setattr(tree_routes, "verify_bearer",
                        lambda request: {"issued_to": "tree-reader", "_is_user_jwt": True})
    monkeypatch.setattr(tree_routes, "has_permission", lambda *args: True)

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
    shown = tree_routes.get_groups_tree("archive", request=_tree_request(), include_terminal=False, include_archived=True, include_summary=False)
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
