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

def test_active_attempt_fallback_cause(clock, decided, starts, fork_join, completions, monkeypatch):
    key, doc, rr = _reviewed_ai_result(clock, monkeypatch, starts)
    store = connection.get_store(); real = store._fetch_one
    monkeypatch.setattr(store, "_fetch_one", lambda sql, params=():
        None if sql.startswith("SELECT x.exec_id FROM graph_attempt_runs x JOIN graph_runs") else real(sql, params))
    assert legacy.node_running_cause(FA, key) == "graph_exec:"+rr["intent_exec_id"]
    assert legacy.node_running(FA, key)
def test_execution_cause_order_is_created_at_then_exec_id(clock, decided, starts, fork_join, completions, monkeypatch):
    key, doc, rr = _reviewed_ai_result(clock, monkeypatch, starts)
    a2 = new_attempt(clock, key, rr)
    eid = exec_for(clock, a2, rr["run_id"], "air_new_graph", status="requested")
    _sql(clock["db"], "UPDATE graph_attempt_runs SET created_at=? WHERE exec_id=?", (OLD, rr["intent_exec_id"]))
    assert legacy.node_running_cause(FA, key) == "graph_exec:"+eid
    _sql(clock["db"], "UPDATE graph_attempt_runs SET created_at=? WHERE exec_id=?", (OLD, eid))
    assert legacy.node_running_cause(FA, key) == "graph_exec:"+max(eid, rr["intent_exec_id"])
def test_document_cause_order_is_result_instruction_then_sorted_links(clock, monkeypatch):
    monkeypatch.setattr(review_stage, "ai_working", lambda *a: False)
    monkeypatch.setattr(run_store, "active_run", lambda _: {"run_id": "run_order"})
    monkeypatch.setattr(run_store, "node_runs", lambda _: [{"node_key": "key", "result_doc_id": "z_result", "instruction_doc_id": "z_instruction"}])
    store = connection.get_store(); real = store._fetch_all
    monkeypatch.setattr(store, "_fetch_all", lambda sql, params=():
        [{"doc_id": "b_link"}, {"doc_id": "a_link"}] if sql.startswith("SELECT doc_id FROM graph_node_documents") else real(sql, params))
    for winner, expected in [("z_result", ["z_result"]), ("z_instruction", ["z_result", "z_instruction"]),
                             ("b_link", ["z_result", "z_instruction", "a_link", "b_link"])]:
        seen = []
        def running(flow, key, doc):
            seen.append(doc)
            return {"run_id": doc} if doc == winner else None
        monkeypatch.setattr(legacy, "running_review_loop", running)
        assert legacy.node_running_cause(FA, "key") == "review_loop:"+winner
        assert seen == expected
