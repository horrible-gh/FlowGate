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

def test_6_a_revision_already_asked_or_judged_is_not_asked_again_and_a_new_one_is(env, decided, monkeypatch, starts):
    a, _a2, result, latest = _spent(env, monkeypatch, starts, "step", follow=False)
    command(env, "step")
    rr2 = one(env, a, "result_review", 2)
    _loop_row(result, LOOP_1, "rework")                             # its loop ended with no new revision
    _loop_end(env, rr2["intent_exec_id"], result, LOOP_1, "review_count_exhausted")
    assert one(env, a, "result_review", 2)["verdict"] == EXHAUSTED and _doc(result)["doc_review_status"] == "revised"
    for mode in ("step", "full", "step"):
        _read(env)                                                  # the screen's read between the commands
        command(env, mode)                                          # the same revision: asked already
    assert [r["seq"] for r in _reviews(env, a)] == [1, 2] and len(_loops(starts)) == 2
    _revised(result, latest + 1)                                    # a new unreviewed revision
    _review_row(result, latest + 1, "issues", LOOP_1)               # ... judged already
    _read(env)
    command(env, "step")
    assert [r["seq"] for r in _reviews(env, a)] == [1, 2] and len(_loops(starts)) == 2
    _revised(result, latest + 2)                                    # a new one nobody judged
    _read(env)
    command(env, "step")
    assert [r["seq"] for r in _reviews(env, a)] == [1, 2, 3] and len(_loops(starts)) == 3
    assert _basis(one(env, a, "result_review", 3))["revision_no"] == latest + 2
def test_6_b_a_revision_rejected_and_submitted_again_is_the_same_revision_and_is_not_asked_again(
        env, decided, monkeypatch, starts):
    """Rework review P2: the revision seq 2 asked is reviewed ``issues``, rejected for that review (its
    ``rejection_history`` names it) and submitted again with its number kept (``rejected`` + submit -> ``revised``).
    Its submission mark differs from seq 2's basis (``rejection_count`` 1) and the shared ``submission_judged`` counts
    it a new submission (left as it is) — yet it is the revision already asked and reviewed: repeated step / full
    commands open no ``seq`` and no loop. A new unreviewed revision is asked once."""
    from flowgate.card_settings import assigned_review, legacy_adapter
    from modules.flow_gate.db import documents as db_documents

    a, _a2, result, latest = _spent(env, monkeypatch, starts, "step", follow=False)
    command(env, "step")
    rr2 = one(env, a, "result_review", 2)
    assert _basis(rr2) == {"doc_id": result, "revision_no": latest, "rejection_count": 0}
    review = _review_row(result, latest, "issues", LOOP_1)          # seq 2's loop reviewed the revision ...
    _loop_row(result, LOOP_1, "review")
    db_documents.update(result, {"doc_review_status": "rejected",   # ... rejected for that review, no rework
                                 "rejection_history": json.dumps([{"reason": "issues", "review_id": review["id"]}])})
    _loop_end(env, rr2["intent_exec_id"], result, LOOP_1, "review_count_exhausted")
    assert one(env, a, "result_review", 2)["verdict"] == EXHAUSTED
    db_documents.update(result, {"doc_review_status": "revised"})   # submitted again: the same revision number
    assert int(_doc(result)["revision_no"]) == latest
    assert _mark(result) == {"doc_id": result, "revision_no": latest, "rejection_count": 1}
    assert _mark(result) != _basis(rr2)                             # the submission mark moved, the revision did not
    assert legacy_adapter.submission_judged(result) is False        # the shared resubmission judgement, unchanged
    run_id = legacy_adapter.active_run(FA)["run_id"]
    assert assigned_review.command_review_due(FA, run_id, legacy_adapter.node_run(run_id, a)) is None
    for mode in ("step", "full", "step", "full"):
        _read(env)                                                  # the screen's read between the commands
        command(env, mode)                                          # the same revision: asked and reviewed already
    assert [r["seq"] for r in _reviews(env, a)] == [1, 2] and len(_loops(starts)) == 2
    assert _doc(result)["doc_review_status"] == "revised"
    _revised(result, latest + 1)                                    # a new revision nobody asked nor reviewed
    _read(env)
    command(env, "full")
    res = _post_command(env, "step")                                # its re-review works: nothing more
    assert res.status_code in (200, 201, 409), res.text
    assert [r["seq"] for r in _reviews(env, a)] == [1, 2, 3] and len(_loops(starts)) == 3
    assert _basis(one(env, a, "result_review", 3)) == {"doc_id": result, "revision_no": latest + 1,
                                                       "rejection_count": 1}
def test_7_a_held_card_takes_no_re_review(env, decided, monkeypatch, starts):
    from flowgate.card_settings import legacy_adapter

    a, _a2, result, _latest = _spent(env, monkeypatch, starts, "step", follow=False)
    stored(a, "hold", True)
    assert a in legacy_adapter.held_nodes(FA)
    command(env, "step")
    assert len(_reviews(env, a)) == 1 and len(_loops(starts)) == 1
    stored(a, "hold", False)                                        # released: the next command asks it
    command(env, "step")
    assert len(_reviews(env, a)) == 2 and len(_loops(starts)) == 2
def test_7_a_time_machine_operation_takes_no_re_review(env, decided, monkeypatch, starts):
    from flowgate.card_settings import assigned_review, legacy_adapter
    from flowgate.graph_time_machine import operation

    a, _a2, result, _latest = _spent(env, monkeypatch, starts, "step", follow=False)
    monkeypatch.setattr(operation, "busy", lambda flow_id: True)
    assigned_review.command_review(FA, legacy_adapter.active_run(FA)["run_id"])
    assert len(_reviews(env, a)) == 1 and len(_loops(starts)) == 1
