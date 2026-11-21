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

def test_3_a_full_its_pass_completes_the_card_and_opens_the_next_card_in_the_same_command(
        env, decided, monkeypatch, starts):
    a, a2, result, _latest = _spent(env, monkeypatch, starts, "full")
    command(env, "full")
    _pass(env, a, 2, result, LOOP_1)
    assert node_run(env, a)["status"] == "done" and _review_status(result) == "approved"
    assert [r["cause"] for r in _records(env, a)] == ["ai_review_passed"]
    assert one(env, a, "result_review", 2)["verdict"] == "passed"
    assert node_run(env, a2)["status"] == "open"
@pytest.mark.parametrize("then", ["step", "full"])
def test_3_b_step_its_pass_completes_the_card_and_the_next_card_waits_for_the_next_command(
        env, decided, monkeypatch, starts, then):
    a, a2, result, _latest = _spent(env, monkeypatch, starts, "step")
    command(env, "step")
    _pass(env, a, 2, result, LOOP_1)
    assert node_run(env, a)["status"] == "done" and _review_status(result) == "approved"
    assert [r["cause"] for r in _records(env, a)] == ["ai_review_passed"]
    assert node_run(env, a2)["status"] == "pending"                  # the step boundary: never opened at once
    assert ("approved", "pending") in [(p["outcome"], p["status"]) for p in _propagations(env, a)]
    run = _read(env)
    assert run["status"] == "idle" and run["idle_reason"] == "step_done"
    assert json.loads(_run(env)["step_node_keys"]) == [a]
    assert node_run(env, a2)["status"] == "pending"
    command(env, then)
    assert node_run(env, a2)["status"] == "open"
    if then == "step":
        assert json.loads(_run(env)["step_node_keys"]) == [a2]
def test_3_c_step_with_no_next_card_its_pass_completes_the_card_and_the_run(env, decided, monkeypatch, starts):
    a, _a2, result, _latest = _spent(env, monkeypatch, starts, "step", follow=False)
    command(env, "step")
    _pass(env, a, 2, result, LOOP_1)
    assert node_run(env, a)["status"] == "done" and _review_status(result) == "approved"
    assert [r["cause"] for r in _records(env, a)] == ["ai_review_passed"]
    run = _read(env)
    assert (run["status"], run["idle_reason"]) == ("idle", "completed")
def test_3_d_spent_again_it_needs_intervention_again_and_only_a_newer_revision_is_asked_again(
        env, decided, monkeypatch, starts):
    a, _a2, result, _latest = _spent(env, monkeypatch, starts, "step", follow=False)
    command(env, "step")
    again = _spend(env, result, one(env, a, "result_review", 2), LOOP_1)
    _assert_spent(env, a, result, again, seqs=2)
    assert [(d, r) for d, r, _m in b1(env)] == [(result, NOT_PASSED)] * 2
    _read(env)
    assert (_run_row(env)["status"], _run_row(env)["idle_reason"]) == ("idle", "needs_intervention")
    command(env, "full")                                            # its own new unreviewed revision: asked once
    assert [r["seq"] for r in _reviews(env, a)] == [1, 2, 3]
    assert _basis(_reviews(env, a)[-1])["revision_no"] == again
