"""Independently selectable scenarios; shared fixtures remain in test_fg_command_review_0125.py."""
from __future__ import annotations

from test_fg_command_review_0125 import (
    B,
    EXHAUSTED,
    FA,
    LOOP_1,
    LOOP_2,
    LOOP_3,
    MEMO,
    NOT_PASSED,
    REREVIEW_RUN,
    REVIEW_RUN,
    REWORK_RUN,
    ROUNDS,
    X,
    _assert_spent,
    _basis,
    _branches,
    _doc,
    _h,
    _loop_end,
    _loop_row,
    _loops,
    _mark,
    _pass,
    _post_command,
    _propagations,
    _read,
    _records,
    _review_row,
    _review_status,
    _reviewed_ai_result,
    _reviews,
    _revised,
    _run,
    _run_ended,
    _run_row,
    _spend,
    _spent,
    _sql,
    _wire,
    assignments,
    b1,
    command,
    completions,
    db_path,
    decided,
    env,
    flow,
    fork_join,
    invoke,
    json,
    memo_routes,
    node_run,
    one,
    pytest,
    pytestmark,
    roles,
    starts,
    stored,
    wired,
    write_line,
)

def test_3_f_an_explicit_ai_invoke_re_review_of_a_step_run_keeps_its_scope(
        env, decided, monkeypatch, starts, invoke):
    post, requested = invoke
    a, a2, result, _latest = _spent(env, monkeypatch, starts, "step")
    res = post(result, "review", document_review_loop={
        "review_count": 1, "review_criteria": "document_type_default", "rework_timeout_sec": 3600,
        "failure_restart_max_attempts": 0, "total_timeout_sec": 14400, "reviewer_provider_id": B,
        "rework_provider_id": X})
    assert res.status_code == 200, res.text
    review = requested[-1]
    assert _run_row(env)["status"] == "running" and _run(env)["run_mode"] == "step"
    assert len(_reviews(env, a)) == 1                                # the route asks no assignment of its own
    _review_row(result, int(_doc(result)["revision_no"]), "pass", REREVIEW_RUN)
    _loop_row(result, REREVIEW_RUN, "review")
    _loop_end(env, review["graph_exec_id"], result, REREVIEW_RUN, "review_passed")
    assert node_run(env, a)["status"] == "done" and _review_status(result) == "approved"
    assert node_run(env, a2)["status"] == "pending"                  # a step's propagation waits for its command
    command(env, "step")
    assert node_run(env, a2)["status"] == "open"
def test_5_a_person_s_newer_revision_is_reviewed_only_once_a_command_asks_it(env, decided, monkeypatch, starts):
    from flowgate.card_settings import review_stage
    from modules.flow_gate.graph_workflow.api import v01_contact

    a, _a2, result, latest = _spent(env, monkeypatch, starts, "step", follow=False)
    person = latest + 1
    _sql(env["db"], "UPDATE documents SET revision_no = ? WHERE doc_id = ?", (person, result))   # stays revised
    _sql(env["db"], "INSERT INTO workflow_events (event_type, project_id, group_id, actor_user_id, metadata, created_at) "
                    "VALUES ('doc_edited', 'flowgate', ?, 'u_kim', ?, 't')",
         (FA, json.dumps({"doc_id": result, "revision_no": person})))
    v01_contact.after_inbox_edited(FA, result, person, None)        # the inbox edit's contact (no token)
    for _ in range(3):
        _read(env)                                                  # reads / reselections
    review_stage.reconcile(FA)                                      # the automatic reconciliation
    assert len(_reviews(env, a)) == 1 and len(_loops(starts)) == 1
    assert _doc(result)["doc_review_status"] == "revised"
    command(env, "step")
    new = _reviews(env, a)[-1]
    assert new["seq"] == 2 and _basis(new) == _mark(result) and _basis(new)["revision_no"] == person
    assert len(_loops(starts)) == 2
    _pass(env, a, 2, result, LOOP_1)
    assert node_run(env, a)["status"] == "done" and _review_status(result) == "approved"
    run = _read(env)
    assert (run["status"], run["idle_reason"]) == ("idle", "completed")
def test_6_two_commands_judged_together_open_one_seq(env, decided, monkeypatch, starts):
    """Two commands whose judgement outside the guard both found the card due: the guard judges again, one wins."""
    from flowgate.card_settings import assigned_review, legacy_adapter

    a, _a2, result, _latest = _spent(env, monkeypatch, starts, "step", follow=False)
    from flowgate.graph_run import command as commands
    from modules.flow_gate.graph_workflow.services import run_apply
    from modules.flow_gate.graph_workflow.services.flow_guard import with_flow_guard
    from modules.flow_gate.graph_workflow.store import run_store

    # The continuation must belong to a fresh explicit command, not the spent writer's command.
    def begin_command():
        old = legacy_adapter.active_run(FA)
        assert run_store.set_run_status(old["run_id"], "idle", "running") == 1
        ctx = run_apply.load_context({**old, "status": "running"}, "flowgate", "u_kim", [])
        commands.record_command(ctx, "step")
        run_apply.emit_run_status(ctx, "idle", "running", "user_command")
        assert run_store.set_run_command(old["run_id"], "step", [a]) == 1
        return run_store.run_by_id(old["run_id"])

    run_row = with_flow_guard(FA, begin_command)
    outside = legacy_adapter.node_run(run_row["run_id"], a)
    stale = assigned_review.rerequest_due(FA, run_row, outside, None, idle_ok=True)
    assert stale is not None
    real = assigned_review.rerequest_due
    monkeypatch.setattr(assigned_review, "rerequest_due",
                        lambda f, r, row, attempt, **kw: stale if row is outside else real(f, r, row, attempt, **kw))
    assigned_review._command_review_row(FA, run_row, outside)
    assigned_review._command_review_row(FA, run_row, outside)       # the second command: its outside view is stale
    assert [r["seq"] for r in _reviews(env, a)] == [1, 2] and len(_loops(starts)) == 2
def test_6_a_command_while_the_first_re_review_works_asks_nothing_more(env, decided, monkeypatch, starts):
    a, _a2, result, _latest = _spent(env, monkeypatch, starts, "step", follow=False)
    command(env, "step")
    for mode in ("step", "full"):
        res = _post_command(env, mode)
        assert res.status_code in (200, 201, 409), res.text          # a running run refuses a command (409)
    assert [r["seq"] for r in _reviews(env, a)] == [1, 2] and len(_loops(starts)) == 2
