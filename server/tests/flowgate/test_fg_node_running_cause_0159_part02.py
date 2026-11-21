"""Independently selectable scenarios; shared fixtures remain in test_fg_node_running_cause_0159.py."""
from __future__ import annotations

from test_fg_node_running_cause_0159 import (
    B,
    BEFORE,
    FA,
    FUTURE,
    Mock,
    NOW,
    OLD,
    RID,
    X,
    _doc,
    _first_hops,
    _h,
    _loop_row,
    _pass,
    _read,
    _review_row,
    _reviewed_ai_result,
    _sql,
    _wire,
    active_loop,
    admission,
    clock,
    command,
    completions,
    connection,
    db_path,
    decided,
    documents,
    edge,
    end_record,
    ended,
    env,
    exec_for,
    flow,
    fork_join,
    gate_policy,
    group_ai_leases,
    ledger,
    legacy,
    loops,
    new_attempt,
    node,
    node_run,
    old_loop,
    one,
    ports,
    put_draft,
    pytest,
    remote,
    review_stage,
    roles,
    run_store,
    runs,
    service,
    starts,
    stored,
    sweep,
    time,
    timedelta,
    wired,
)

@pytest.mark.parametrize("status,old", [("requested", False), ("requested", True), ("running", False)])
def test_graph_cause_including_older_attempt(clock, decided, starts, fork_join, completions, monkeypatch, status, old):
    key, doc, rr = _reviewed_ai_result(clock, monkeypatch, starts)
    _sql(clock["db"], "UPDATE graph_attempt_runs SET status=?,lease_expires_at=? WHERE exec_id=?",
         (status, FUTURE, rr["intent_exec_id"]))
    if old: new_attempt(clock, key, rr)
    assert legacy.node_running_cause(FA, key) == "graph_exec:" + rr["intent_exec_id"]
    assert legacy.node_running(FA, key)
@pytest.mark.parametrize("with_execution", [False, True])
def test_group_lease_cause(clock, decided, starts, fork_join, completions, monkeypatch, with_execution):
    key, doc, rr = _reviewed_ai_result(clock, monkeypatch, starts)
    ended(clock, rr["intent_exec_id"], RID)
    token = _sql(clock["db"], "SELECT token_id FROM graph_run_token_map WHERE run_id=? AND node_key=? LIMIT 1", (rr["run_id"], key))[0]["token_id"]
    lease_run = RID if with_execution else "air_lease_only"
    _sql(clock["db"], "UPDATE tokens SET ai_run_id=? WHERE token_id=?", (lease_run, token))
    monkeypatch.setattr(legacy.db_leases, "get", lambda _: {"run_id": lease_run, "expires_at": FUTURE})
    cause = "graph_exec:" + rr["intent_exec_id"] if with_execution else "group_lease:" + lease_run
    assert legacy.node_running_cause(FA, key) == cause
    assert legacy.node_running(FA, key)
def test_cause_priority_and_patch_compatibility(clock, decided, starts, fork_join, completions, monkeypatch):
    key, doc, rr = _reviewed_ai_result(clock, monkeypatch, starts)
    active_loop(clock, doc=doc)
    assert legacy.node_running_cause(FA, key) == "graph_exec:" + rr["intent_exec_id"]
    monkeypatch.setattr(review_stage, "_handoff_pending", lambda *a: True)
    assert legacy.node_running_cause(FA, key) == "handoff"
    monkeypatch.setattr(review_stage, "_handoff_pending", lambda *a: False)
    ended(clock, rr["intent_exec_id"], RID)
    assert legacy.node_running_cause(FA, key) == "review_loop:" + RID
    monkeypatch.setattr(legacy, "review_loops", lambda _: [])
    monkeypatch.setattr(legacy, "review_loop_active", lambda _: True)
    assert legacy.node_running_cause(FA, key) == "review_loop:unknown"
    monkeypatch.setattr(legacy, "node_ai_run_active", lambda *a: True)
    assert legacy.node_running_cause(FA, key) == "graph_exec:unknown"
    assert legacy.node_running(FA, key)
