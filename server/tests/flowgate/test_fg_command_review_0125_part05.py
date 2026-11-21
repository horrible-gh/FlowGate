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

def test_7_another_run_than_the_flow_s_active_one_takes_no_re_review(env, decided, monkeypatch, starts):
    from flowgate.card_settings import assigned_review

    a, _a2, result, _latest = _spent(env, monkeypatch, starts, "step", follow=False)
    assigned_review.command_review(FA, "run_not_the_active_one")
    assert len(_reviews(env, a)) == 1 and len(_loops(starts)) == 1
@pytest.mark.parametrize("status", ["rejected", "pending_review"])
def test_7_an_explicit_command_recovers_a_rejected_or_pending_document(env, decided, monkeypatch, starts, status):
    from modules.flow_gate.db import documents as db_documents

    a, _a2, result, _latest = _spent(env, monkeypatch, starts, "step", follow=False)
    db_documents.update(result, {"doc_review_status": status})
    command(env, "step")
    assert len(_reviews(env, a)) == 2 and len(_loops(starts)) == 2
def test_7_completion_missing_with_unapproved_output_recovers_at_review(env, decided, monkeypatch, starts):
    a, _a2, result, _latest = _spent(env, monkeypatch, starts, "step", follow=False)
    _sql(env["db"], "DELETE FROM graph_run_events WHERE cause = 'auto_approval_skipped'")   # no reason recorded
    assert _wire(env, a)["intervention"] == {"kind": "needs_intervention", "reason": "completion_missing"}
    command(env, "step")
    assert len(_reviews(env, a)) == 2 and len(_loops(starts)) == 2
def test_7_a_system_error_takes_no_re_review_and_keeps_its_own_retry(env, decided, monkeypatch, starts):
    from flowgate.card_settings import legacy_adapter

    key = flow(env, extra_settings=(("instruction", "omit"), ("review_count", ROUNDS)))
    roles(key, execution=X, review=B)
    command(env, "step")
    a, result, rr = _reviewed_ai_result(env, monkeypatch, starts, key)
    _revised(result, int(rr["basis_revision_no"]) + 1)              # a reworked revision, passed by its loop
    monkeypatch.setattr(legacy_adapter, "submission_passed", lambda doc_id: True)
    monkeypatch.setattr(legacy_adapter, "completion_gates", lambda doc: (_ for _ in ()).throw(RuntimeError("refused")))
    _loop_row(result, REVIEW_RUN, "review")
    _loop_end(env, rr["intent_exec_id"], result, REVIEW_RUN, "review_passed")
    assert _wire(env, a)["intervention"]["kind"] == "system_error"
    command(env, "step")
    assert len(_reviews(env, a)) == 1 and len(_loops(starts)) == 1
    assert one(env, a, "result_review", 1)["verdict"] == "passed"
