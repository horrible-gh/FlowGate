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
