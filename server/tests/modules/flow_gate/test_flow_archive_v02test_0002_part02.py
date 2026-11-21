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
