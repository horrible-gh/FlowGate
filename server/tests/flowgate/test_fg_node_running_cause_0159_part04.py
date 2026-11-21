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

@pytest.mark.parametrize("case", ["none", "exception", "unregistered", "idle_open", "idle_review", "done"])
def test_actual_run_read_cause_defaults(clock, decided, starts, fork_join, completions, monkeypatch, case):
    if case == "idle_open":
        key = flow(clock, extra_settings=(("instruction", "omit"),))
        roles(key, instruction="not_used", execution="not_used", review="not_used")
        command(clock, "step")
        wire = _wire(clock, key)
        assert wire["status"] == "open"
        assert (wire["node_running"], wire["node_running_cause"]) == (False, None)
        return
    key, doc, rr = _reviewed_ai_result(clock, monkeypatch, starts)
    ended(clock, rr["intent_exec_id"], RID)
    if case in ("none", "exception"):
        monkeypatch.setattr(ports, "node_running", lambda *a: True)
        ports.register(ports.NODE_RUNNING_CAUSE, Mock(side_effect=RuntimeError("read failed")) if case == "exception" else lambda *a: None)
    elif case == "unregistered":
        ports.unregister(ports.NODE_RUNNING); ports.unregister(ports.NODE_RUNNING_CAUSE)
    else:
        status = {"idle_open": "open", "idle_review": "review", "done": "done"}[case]
        _sql(clock["db"], "UPDATE graph_node_runs SET status=? WHERE node_key=?", (status, key))
    wire = _wire(clock, key)
    if case in ("idle_review", "done"):
        assert wire["status"] == status
    assert "node_running_cause" in wire
    assert (wire["node_running"], wire["node_running_cause"]) == ((True, "unknown") if case in ("none", "exception") else (False, None))
def test_wrapper_delegates_grace_without_writes(clock, monkeypatch):
    row = active_loop(clock)
    before = loops.get(RID)
    held = {"some-owner"}
    monkeypatch.setattr(admission, "_restart_held_run_ids", lambda: held)
    evidence = Mock(return_value="recent")
    monkeypatch.setattr(admission, "review_loop_owner_evidence", evidence)
    assert service.review_loop_owner(row, now=NOW) == "recent"
    evidence.assert_called_once_with(row, held=held, before=NOW-timedelta(seconds=900), now=NOW)
    monkeypatch.setattr(admission, "_restart_held_run_ids", lambda: None)
    assert service.review_loop_owner(row, now=NOW) == "remote_hold"
    assert evidence.call_count == 1 and loops.get(RID) == before
    monkeypatch.setattr(admission, "_restart_held_run_ids", Mock(side_effect=RuntimeError("unreadable")))
    with pytest.raises(RuntimeError): service.review_loop_owner(row, now=NOW)
@pytest.mark.parametrize("recovery", ["sweep", "restart"])
def test_issues_stuck_recovered_rework_revision_rereview_followup_no_no_targets(
        clock, decided, starts, gate_policy, completions, monkeypatch, recovery):
    graph = put_draft(clock, FA, 0, [node("tmp_r", "R"), node("tmp_a", "T"), node("tmp_gate", node_type="gate")],
                      [edge("tmp_e1", "tmp_r", "tmp_a"), edge("tmp_e2", "tmp_a", "tmp_gate")])
    key, follow = graph["key_map"]["tmp_a"], graph["key_map"]["tmp_gate"]
    stored(key, "instruction", "omit"); stored(key, "review_count", 1)
    res = clock["client"].post(f"/api/v1/flows/{FA}/graph/revisions", json={"draft_version": 1}, headers=_h())
    assert res.status_code == 201, res.text
    roles(key, execution=X, review=B)
    command(clock, "full")
    key, doc, rr = _reviewed_ai_result(clock, monkeypatch, starts, key)
    assert doc.endswith("-TR")
    _review_row(doc, 0, "issues", RID)
    documents.update(doc, {"doc_review_status": "rejected", "rejection_reason": "fix findings"})
    active_loop(clock, doc=doc)
    _sql(clock["db"], "UPDATE ai_invoke_document_review_loops SET deadline_at=? WHERE run_id=?", (FUTURE, RID))
    ended(clock, rr["intent_exec_id"], RID)
    end_record(RID, doc)
    original_end = runs.get(RID)
    assert group_ai_leases.get_by_run_id(RID) is None
    assert (_wire(clock, key)["node_running"], _wire(clock, key)["node_running_cause"]) == (True, "review_loop:"+RID)
    if recovery == "sweep":
        assert service.sweep_unowned_review_loops(now=NOW) == 0
        assert service.sweep_unowned_review_loops(now=NOW) == 1
        reason = "retry_exhausted"
        service.startup_recover_leases.assert_not_called()
    else:
        monkeypatch.setattr(admission, "_startup_recovery_before", None)
        monkeypatch.setattr(service, "startup_recover_handoffs", lambda: 0)
        from modules.flow_gate.services import q_service
        monkeypatch.setattr(q_service, "startup_recover_question_responders", lambda: 0)
        assert admission.startup_recover_leases() == 0
        reason = "restart_orphaned"
    wire = _wire(clock, key)
    assert (wire["node_running"], wire["node_running_cause"]) == (False, None)
    assert wire["intervention"] == {"kind": "needs_intervention", "reason": "review_not_passed:"+reason}
    assert runs.get(RID) == original_end
    assert documents.get_by_id(doc)["revision_no"] == 0
    hops = _first_hops(monkeypatch)
    resumed = command(clock, "full")
    assert resumed.get("idle_reason") != "no_targets" and hops == ["rework"]
    rr2 = one(clock, key, "result_review", 2)
    assert rr2["stage_state"] == "active" and rr2["seq"] == rr["seq"]+1
    # The recorded AI performs its rework; retain the original issues review and write a new revision.
    documents.update(doc, {"revision_no": 1, "doc_review_status": "revised"})
    assert documents.get_by_id(doc)["revision_no"] == 1
    _pass(clock, key, 2, doc, "air_followup_pass_0159")
    state = _read(clock)
    assert node_run(clock, key)["status"] == "done"
    assert node_run(clock, follow)["status"] == "open"
    assert legacy.node_of_key(FA, follow)["node_type"] == "gate"
    assert state.get("idle_reason") != "no_targets"
    assert _sql(clock["db"], "SELECT verdict,revision_no FROM document_reviews WHERE doc_id=? ORDER BY id", (doc,)) == [
        {"verdict": "issues", "revision_no": 0}, {"verdict": "pass", "revision_no": 1}]
    for _ in range(3):
        assert command(clock, "full").get("idle_reason") != "no_targets"
        assert node_run(clock, follow)["status"] == "open"
def test_old_loop_guard_window_until_sweeper_closes(clock, decided, starts, fork_join, completions, monkeypatch):
    key, doc, rr, a2 = old_loop(clock, monkeypatch, starts, old_document=True)
    assert not legacy.node_running(FA, key) and legacy.review_loop_active(doc)
    # Exercise the unmodified broad start guard, even while R4 excludes the loop.
    run = run_store.active_run(FA)
    start_loop = Mock()
    monkeypatch.setattr(legacy, "start_review_loop", start_loop)
    before = len(starts)
    assert review_stage.review_running(FA, key, doc) is False
    review_stage._start_person_review(FA, run, key, documents.get_by_id(doc), {"review_ai": B}, "u_kim", a2)
    start_loop.assert_not_called()
    assert service.sweep_unowned_review_loops(now=NOW) == 0
    assert legacy.review_loop_active(doc)
    assert service.sweep_unowned_review_loops(now=NOW) == 1
    assert not legacy.review_loop_active(doc)
    assert len(starts) == before
    review_stage._start_person_review(FA, run, key, documents.get_by_id(doc), {"review_ai": B}, "u_kim", a2)
    start_loop.assert_called_once()
