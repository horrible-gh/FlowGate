"""Independently selectable scenarios; shared fixtures remain in test_fg_card_approval_0056.py."""
from __future__ import annotations

from test_fg_card_approval_0056 import (
    AI,
    FA,
    LEE,
    Path,
    REVIEW_RUN,
    TS_AUTOMATED,
    TS_INVALID,
    WORK_RUN,
    _GatePolicy,
    _SERVER_DIR,
    _STOPPED_AI_RUNS,
    _admin_and_reviewer,
    _approval_route_gone,
    _causes_after,
    _chain,
    _current,
    _doc,
    _doc_events,
    _events,
    _h,
    _human_result,
    _imports,
    _in_review,
    _lease,
    _line_doc,
    _loop_row,
    _node,
    _node_run,
    _recorded_start_run,
    _reopened,
    _result,
    _resume,
    _review_status,
    _run_id,
    _run_one_card,
    _sql,
    _start,
    _stop,
    _submit_written,
    _ts_instruction,
    _ts_runs,
    _v01_approve,
    _work_run_ended,
    _write_body,
    ast,
    card_outputs_port,
    db_path,
    decided,
    edge,
    env,
    gate_decided,
    legacy_cards,
    no_v01_path,
    node,
    pre_feature_attempts,
    put_draft,
    pytest,
    pytestmark,
    setting,
    ts_admission,
)

def test_a_gate_card_keeps_its_own_confirm(pre_feature_attempts, env, gate_decided, monkeypatch):
    _admin_and_reviewer(env)
    t1, gate = _chain(env, [("T", LEE), ("gate", None)])
    _start(env)
    # T-2 / T-3 rework: t1 completes automatically as submitted (review 'none'), no person's card approval
    _human_result(env, t1, state="done")
    assert _node(env, gate)[0] == "open"
    _approval_route_gone(env, gate)
    assert _node_run(env, gate)["awaiting_approval"] is False
    decided_run = env["client"].post(f"/api/v1/runs/{_run_id(env)}/nodes/{gate}/gate-decision",
                                     json={"decision": "confirm"}, headers=_h())
    assert decided_run.status_code == 200 and _node(env, gate)[0] == "done"   # the gate keeps its own decision
def test_a_person_s_rejection_is_no_node_event(pre_feature_attempts, env, decided, monkeypatch):
    from modules.flow_gate.graph_workflow.api import v01_contact
    from modules.flow_gate.workflow.pipeline_service import transition_document_review
    from test_fg_card_settings import legacy_setting

    _admin_and_reviewer(env)
    (t1,) = _chain(env, [("T", LEE)])
    for field, value in (("review_count", 1), ("review_ai", "aip_review")):   # its card reviews it (0119 T-3 rework)
        legacy_setting(env, FA, t1, field, value)
    calls = _recorded_start_run(monkeypatch)
    _start(env)
    result = _human_result(env, t1)                                      # in review while its review works
    assert len({c.get("graph_exec_id") for c in calls}) == 1           # its one review (a redelivery counted once)
    before = _current(env)["last_event_seq"]
    transition_document_review(doc_id=result, action="reject", actor_user_id="u_kim",
                               user_permissions={"document.reject"}, comment="다시")
    v01_contact.after_review_decided(FA, result, "reject")             # the v0.1 reject route's contact
    run = _current(env)                                                  # reconciliation ran before the read
    node_run = next(n for n in run["node_runs"] if n["node_key"] == t1)
    assert (node_run["status"], run["last_event_seq"]) == ("review", before)     # no node event, no approval
    assert _review_status(result) == "rejected" and len({c.get("graph_exec_id") for c in calls}) == 1
    _approval_route_gone(env, t1)
    assert (_node(env, t1)[0], _review_status(result)) == ("review", "rejected")
def test_a_review_that_spent_its_count_leaves_the_card_in_intervention(env, decided, monkeypatch):
    from modules.flow_gate.db import documents as db_documents
    from modules.flow_gate.services import ai_run_end_hooks

    key, result, _token = _in_review(env, monkeypatch, (("review_count", 1), ("review_ai", "aip_review"), ("after_review_exhausted", "stop")))
    _loop_row(result, "review")
    db_documents.update(result, {"doc_review_status": "rejected"})       # the last review hop rejected it
    _sql(env["db"], "UPDATE ai_invoke_document_review_loops SET current_stage = 'stopped', "
                    "stop_reason = 'review_count_exhausted', stop_detail = 'spent' WHERE run_id = ?", (REVIEW_RUN,))
    ai_run_end_hooks.run_ended({"run_id": REVIEW_RUN, "group_id": FA, "doc_ref": result, "action_scope": "review",
                                "mode": "single", "issued_to": "u_kim",
                                "document_review_loop": {"current_stage": "stopped"}})
    assert _node(env, key) == ("review", result)
    assert _node_run(env, key)["intervention"] == {"kind": "needs_intervention",
                                                   "reason": "review_not_passed:review_count_exhausted"}
    _approval_route_gone(env, key)                                       # T-3: no person's approval
    assert (_node(env, key)[0], _review_status(result)) == ("review", "rejected")
def test_0056_T1_개정_rejected_root_is_input_and_its_card_is_done(env, decided):
    """revised: Run accepts rejected root input; its document stays rejected."""
    from modules.flow_gate.db import documents as db_documents

    _admin_and_reviewer(env)
    (t1,) = _chain(env, [("T", LEE)])
    root = f"{FA}.0001-R"
    _write_body(root, "\n# 요구사항\n\n반려된 요구사항\n")
    db_documents.update(root, {"doc_review_status": "rejected"})        # a person rejected the root (v0.1)
    _start(env)
    run = _current(env)
    root_run = next(n for n in run["node_runs"] if n["root_bound"])
    r_key = root_run["node_key"]
    assert (root_run["status"], root_run["result_doc_id"], root_run["awaiting_approval"]) == ("done", root, False)
    assert "review_rejected" not in _events(env, r_key) and run["open_node_keys"] == [t1]
    assert _sql(env["db"], "SELECT outcome FROM graph_propagations WHERE node_key = ?", (r_key,)) == [
        {"outcome": "approved"}]
    _approval_route_gone(env, r_key)
    assert _review_status(root) == "rejected"                            # nothing written
    run = _current(env)                                                  # the first work card follows the root input
    nodes = {n["node_key"]: n for n in run["node_runs"]}
    assert (nodes[r_key]["status"], nodes[t1]["status"], run["open_node_keys"]) == ("done", "open", [t1])
@pytest.mark.parametrize("review_count", [0, 2])
@pytest.mark.parametrize("end_reason, stop_code", _STOPPED_AI_RUNS)
def test_an_ai_run_stopped_after_binding_its_result_leaves_the_card_in_intervention(
        env, decided, monkeypatch, end_reason, stop_code, review_count):
    settings = (("review_count", review_count), ("review_ai", "aip_review")) if review_count else ()
    key, result, _token = _in_review(env, monkeypatch, settings)          # the result is bound (outcome complete)
    calls = _recorded_start_run(monkeypatch)
    _work_run_ended(end_reason=end_reason, stop_code=stop_code)
    assert calls == []                                                   # no review loop
    assert (_node(env, key), _review_status(result)) == (("review", result), "pending_review")   # no approval
    shown = _node_run(env, key)                                          # T-2: the AI's abnormal end
    assert (shown["awaiting_approval"], shown["intervention"]["kind"]) == (False, "needs_intervention")
    assert shown["intervention"]["reason"].startswith("run_not_clean:")
    _approval_route_gone(env, key)
    assert _node(env, key) == ("review", result)
def test_only_a_run_that_ended_on_its_own_is_a_clean_end():
    from flowgate.card_settings.review_stage import ended_cleanly

    assert ended_cleanly({"end_reason": "exited", "stop_code": None, "outcome": "complete"})
    assert ended_cleanly({"end_reason": "exited"})
    assert not any(ended_cleanly({"end_reason": e, "stop_code": c, "outcome": "complete"}) for e, c in _STOPPED_AI_RUNS)
    assert not ended_cleanly({"end_reason": "exited", "stop_code": "question_pending", "outcome": "complete"})
    assert not ended_cleanly({"outcome": "complete"})                    # no end reason: not judged clean
def test_an_automatically_approved_card_never_goes_back_to_awaiting_approval_while_stopped(
        env, decided, monkeypatch):
    key, result, _token = _in_review(env, monkeypatch)                   # an AI card, no review
    _stop(env)
    _work_run_ended()                                                    # the card approves it at once
    assert _node(env, key) == ("done", result) and _review_status(result) == "approved"
    for _ in range(2):                                                   # reads reconcile: nothing goes back
        run = _current(env)
        node_run = next(n for n in run["node_runs"] if n["node_key"] == key)
        assert (run["status"], node_run["status"], node_run["awaiting_approval"]) == ("stopped", "done", False)
        assert (run["pending_propagation_count"], run["pending_node_keys"]) == (1, [key])
    _approval_route_gone(env, key)
    assert _resume(env)["status"] == "completed"
