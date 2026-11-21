"""Independently selectable scenarios; shared fixtures remain in test_graph_run_api_0045.py."""
from __future__ import annotations

from tests.modules.flow_gate.test_graph_run_api_0045 import (
    FG,
    FH,
    FU,
    FV,
    FastAPI,
    JSONResponse,
    Path,
    RUN_TABLES,
    TEST_VALUE,
    TEST_VERSION,
    TestClient,
    _MIGRATIONS_DIR,
    _POLICY,
    _SERVER_DIR,
    _TestPolicy,
    _confirm,
    _confirm_next,
    _doc,
    _edge,
    _fake_ai_invoke,
    _h,
    _node,
    _seed,
    _sql,
    control,
    count,
    current,
    db_path,
    decide_review,
    decided,
    env,
    hashlib,
    json,
    nodes_of,
    os,
    pytest,
    run_events,
    sqlite3,
    start,
    submit,
    sys,
    tempfile,
    token_of,
)

def test_results_move_the_run_to_completion(env, decided):
    run_id = start(env).json()["run"]["run_id"]
    d = _doc(FG, 2, "D")
    submit(FG, d, "D", token_of(env, "gn_d"))
    decide_review(FG, d, "approve", "approved")
    p_run = nodes_of(current(env))["gn_p"]
    assert p_run["status"] == "open" and p_run["dispatch"] == {
        "token_issued": True, "mention_id": None, "issued_to": "claude-code", "error_code": "ai_invoke_failed"}
    p_token = _sql(env["db"], "SELECT * FROM tokens WHERE token_id = ?", (token_of(env, "gn_p"),))[0]
    assert p_token["issued_to"] == "u_kim" and p_token["provider_id"] == "claude-code"
    p = _doc(FG, 3, "P")
    submit(FG, p, "P", token_of(env, "gn_p"))
    decide_review(FG, p, "approve", "approved")
    assert run_events(env)[3:] == [
        (4, "graph_node_state_changed", "gn_d", "result_submitted"),
        (5, "graph_node_state_changed", "gn_d", "review_approved"),
        (6, "graph_node_state_changed", "gn_p", "opened"),
        (7, "graph_node_state_changed", "gn_p", "dispatch_failed"),
        (8, "graph_node_state_changed", "gn_p", "result_submitted"),
        (9, "graph_node_state_changed", "gn_p", "review_approved"),
        (10, "graph_run_status_changed", None, "completed")]
    assert env["events"][-1].payload["actor_user_id"] is None
    head = _sql(env["db"], "SELECT status, active_group_id, finished_at, last_event_seq FROM graph_runs")[0]
    assert head["status"] == "completed" and head["active_group_id"] is None and head["finished_at"]
    assert head["last_event_seq"] == 10 and current(env) is None
    got = env["client"].get(f"/api/v1/runs/{run_id}", headers=_h()).json()["run"]
    assert {k: v["result_doc_id"] for k, v in nodes_of(got).items()} == {"gn_r": f"{FG}.0001-R", "gn_d": d, "gn_p": p}
    listed = env["client"].get(f"/api/v1/flows/{FG}/runs?limit=5", headers=_h()).json()
    assert listed["total"] == 1 and listed["items"][0]["status"] == "completed" and "node_runs" not in listed["items"][0]
def test_documents_that_are_not_results_bind_nothing(env, decided):
    start(env)
    seq = current(env)["last_event_seq"]
    submit(FG, _doc(FG, 2, "D"), "D", None)                         # no token: Q-12, not bound
    submit(FG, _doc(FG, 3, "M"), "M", token_of(env))                # the node's token, another type (E-24)
    decide_review(FG, f"{FG}.0002-D", "approve", "approved")        # not a node result
    assert current(env)["last_event_seq"] == seq and nodes_of(current(env))["gn_d"]["status"] == "open"
    d = _doc(FG, 4, "D")
    submit(FG, d, "D", token_of(env))
    submit(FG, _doc(FG, 5, "D"), "D", token_of(env))                # a second result is not bound
    decide_review(FG, d, "approve", "approved")
    after = current(env)["last_event_seq"]
    decide_review(FG, d, "approve", "approved")                     # repeated: ignored, no event_seq
    assert current(env)["last_event_seq"] == after and nodes_of(current(env))["gn_d"]["result_doc_id"] == d
def test_a_revision_replaces_the_result_and_a_chain_gap_is_walked(env, decided):
    from modules.flow_gate.db import documents as db_documents

    start(env)
    d1 = _doc(FG, 2, "D")
    submit(FG, d1, "D", token_of(env))
    decide_review(FG, d1, "reject", "rejected")                      # flowgate.v02.0056 T#1: no node event
    assert nodes_of(current(env))["gn_d"]["status"] == "review"
    d2 = _doc(FG, 3, "D", "rejected")                              # revision of d1, rejected, both unreported
    db_documents.set_superseded_by(d1, d2)
    d3 = _doc(FG, 4, "D", "pending_review")
    db_documents.set_superseded_by(d2, d3)
    submit(FG, d3, "D", None)                                      # gap d1 -> d2 -> d3: walk it
    node = nodes_of(current(env))["gn_d"]
    assert node["status"] == "review" and node["result_doc_id"] == d3
    assert [c for _s, _t, k, c in run_events(env) if k == "gn_d"][-3:] == ["result_submitted", "revised", "revised"]
    d4 = _doc(FG, 5, "D")
    db_documents.set_superseded_by(d3, d4)
    submit(FG, d4, "D", None)                                      # direct revision (one hop)
    assert nodes_of(current(env))["gn_d"]["result_doc_id"] == d4
def test_stop_and_resume(env, decided):
    run_id = start(env).json()["run"]["run_id"]
    stopped = control(env, run_id, "stop", "설계 재검토")
    assert stopped.status_code == 200 and stopped.json()["run"]["status"] == "stopped"
    assert stopped.json()["run"]["stopped_at"] and "node_runs" not in stopped.json()["run"]
    assert env["events"][-1].payload["reason"] == "설계 재검토" and env["events"][-1].payload["cause"] == "user_stop"
    again = control(env, run_id, "stop")
    assert again.status_code == 409 and again.json()["error"]["params"] == {"run_id": run_id, "status": "stopped"}
    d = _doc(FG, 2, "D")
    submit(FG, d, "D", token_of(env))
    decide_review(FG, d, "approve", "approved")                     # stopped: done, but nothing opens
    held = current(env)
    assert nodes_of(held)["gn_d"]["status"] == "done" and nodes_of(held)["gn_p"]["status"] == "pending"
    # flowgate.v02.0056 T#1: the run band's "멈춤 · 다음 카드 N개 대기"; the approved card does not wait again
    assert (held["pending_propagation_count"], held["pending_node_keys"]) == (1, ["gn_d"])
    assert nodes_of(held)["gn_d"]["awaiting_approval"] is False
    assert _sql(env["db"], "SELECT status FROM graph_propagations WHERE node_key = 'gn_d'") == [{"status": "pending"}]
    resumed = control(env, run_id, "resume")
    assert resumed.status_code == 200 and resumed.json()["run"]["status"] == "running"
    assert resumed.json()["run"]["stopped_at"] is None and resumed.json()["run"]["open_node_keys"] == ["gn_p"]
    assert (resumed.json()["run"]["pending_propagation_count"], resumed.json()["run"]["pending_node_keys"]) == (0, [])
    assert [c for *_x, c in run_events(env)][-3:] == ["user_resume", "opened", "dispatch_failed"]
    assert _sql(env["db"], "SELECT status FROM graph_propagations WHERE node_key = 'gn_d'") == [{"status": "done"}]
    twice = control(env, run_id, "resume")
    assert twice.status_code == 409 and twice.json()["error"]["code"] == "run_not_stopped"
    assert control(env, "run_nope", "stop").status_code == 404
def test_resume_refuses_an_unrestorable_snapshot(env, decided):
    run_id = start(env).json()["run"]["run_id"]
    control(env, run_id, "stop")
    _sql(env["db"], "UPDATE graph_run_policy_entries SET impl_version = 'gone' WHERE policy_key = 'join'")
    from modules.flow_gate.graph_workflow.services.policy_snapshot import check_active_runs

    assert check_active_runs() == [{"policy": "join", "value": TEST_VALUE, "impl_version": "gone"}]
    res = control(env, run_id, "resume")
    assert res.status_code == 500 and res.json()["error"]["code"] == "internal_error"
    assert _sql(env["db"], "SELECT status FROM graph_runs")[0]["status"] == "stopped"     # E-33: nothing written
def test_a_lost_delegation_is_recovered_before_a_run_read(env, decided, monkeypatch):
    from modules.flow_gate.graph_workflow.services import result_binder
    from modules.flow_gate.services import token_service

    start(env)
    real = result_binder.on_document_event
    monkeypatch.setattr(result_binder, "on_document_event", lambda *a: (_ for _ in ()).throw(RuntimeError("lost")))
    d = _doc(FG, 2, "D")
    submit(FG, d, "D", token_of(env))                               # swallowed by the contact
    token_service.consume(token_of(env), "flowgate", d)              # what the inbox records next
    decide_review(FG, d, "approve", "approved")                     # lost as well
    monkeypatch.setattr(result_binder, "on_document_event", real)
    assert _sql(env["db"], "SELECT status FROM graph_node_runs WHERE node_key = 'gn_d'") == [{"status": "open"}]
    run = current(env)
    assert nodes_of(run)["gn_d"]["status"] == "done" and run["open_node_keys"] == ["gn_p"]
    assert [c for *_x, c in run_events(env)][3:6] == ["result_submitted", "review_approved", "opened"]
