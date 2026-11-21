"""Independently selectable scenarios; shared fixtures remain in test_fg_command_rerequest_0124.py."""
from __future__ import annotations

from test_fg_command_rerequest_0124 import (
    A,
    B,
    EXCLUDED,
    FA,
    LIMIT,
    MEMO,
    REVIEW_RUN,
    SECOND_LOOP,
    SPENT,
    THIRD_LOOP,
    X,
    _b1_after,
    _card_spent,
    _chain,
    _counts,
    _fan,
    _first_hops,
    _h,
    _hold,
    _loop_end,
    _loop_row,
    _loops,
    _mark,
    _passes,
    _read,
    _reason,
    _refuse_starts,
    _refused,
    _rerequested_at,
    _result,
    _review_status,
    _reviewed_ai_result,
    _run,
    _run_ended,
    _run_row,
    _spent,
    _sql,
    _stage_assigned,
    _start_ai,
    _wire,
    assignments,
    b1,
    command,
    db_path,
    decided,
    edge,
    env,
    events,
    fork_join,
    json,
    ledger,
    memo_routes,
    node,
    node_run,
    one,
    put_draft,
    pytest,
    record_starts,
    roles,
    starts,
    stored,
    threading,
    traceback,
    wired,
    write_line,
)

def test_the_retries_of_a_re_requested_start_end_at_the_limit_of_its_own_cycle(
        env, decided, monkeypatch, starts, fork_join):
    """The reviewer stays refused: seq 2 (the command's) and the reads' retries are tried ``SYSTEM_ERROR_RETRY_LIMIT``
    times in all, counted from the re-request — then nothing is tried again however often the run is read."""
    a, _a2, _a3, _result, first = _spent(env, monkeypatch, starts)
    tried, _recorder = _refuse_starts(monkeypatch)
    command(env, "step")
    for _ in range(LIMIT + 3):
        _read(env)
    rows = [r for r in assignments(env, a) if r["stage"] == "result_review"]
    assert [r["seq"] for r in rows] == list(range(1, LIMIT + 2))              # seq 1, then LIMIT tries of the cycle
    assert all((r["stage_state"], r["fail_cause"]) == ("failed", "start_refused:ai_disabled") for r in rows[1:])
    assert len(tried) == LIMIT and {reviewer for _e, reviewer in tried} == {B}
    assert one(env, a, "result_review", 1) == first
    assert _b1_after(env, _rerequested_at(env, a), "review_not_started") == [("review_not_started", _mark(first))]
    assert _wire(env, a)["intervention"] == {"kind": "system_error", "reason": "review_not_started"}
def test_a_needs_intervention_reason_recorded_after_the_re_request_still_stops_the_retries(
        env, decided, monkeypatch, starts, fork_join):
    """The cycle only drops the records before the re-request: a needs_intervention reason recorded in it (here an
    abnormal end) stops the retry of the refused start as before — nothing is requested again but by a command."""
    from modules.flow_gate.services import ai_invoke_service

    a, _a2, _a3, result, first = _spent(env, monkeypatch, starts)
    _tried, recorder = _refuse_starts(monkeypatch)
    command(env, "step")
    _reason(env, a, result, first, "run_not_clean:timeout")
    monkeypatch.setattr(ai_invoke_service, "start_run", recorder)
    loops = len(_loops(starts))
    for _ in range(3):
        _read(env)
    assert one(env, a, "result_review", 3) is None and len(_loops(starts)) == loops
def test_a_re_requested_pass_whose_approval_failed_once_completes_on_the_next_read(
        env, decided, monkeypatch, starts, fork_join):
    """seq 2 passes the same submission, its automatic approval is refused once (``approval_refused``, a system error
    of the cycle): the next run read approves it — the seq 1 review_not_passed record of that mark no longer stops it."""
    from flowgate.card_settings import legacy_adapter

    a, a2, _a3, result, first = _spent(env, monkeypatch, starts)
    command(env, "step")
    floor = _rerequested_at(env, a)
    monkeypatch.setattr(legacy_adapter, "completion_gates", _refused)
    _passes(env, monkeypatch, a, result, SECOND_LOOP)
    assert one(env, a, "result_review", 2)["verdict"] == "passed"
    assert node_run(env, a)["status"] == "review" and _review_status(result) != "approved"
    assert _b1_after(env, floor) == [("approval_refused", _mark(first))]
    monkeypatch.setattr(legacy_adapter, "completion_gates", lambda doc: None)  # the cause is gone
    _read(env)
    assert node_run(env, a)["status"] == "done" and _review_status(result) == "approved"
    assert node_run(env, a2)["status"] == "pending"
    assert _b1_after(env, floor) == [("approval_refused", _mark(first))]       # nothing recorded by the retry
def test_the_approval_retries_of_a_re_requested_pass_end_at_the_limit_of_its_own_cycle(
        env, decided, monkeypatch, starts, fork_join):
    from flowgate.card_settings import legacy_adapter

    a, _a2, _a3, result, first = _spent(env, monkeypatch, starts)
    command(env, "step")
    floor = _rerequested_at(env, a)
    monkeypatch.setattr(legacy_adapter, "completion_gates", _refused)
    _passes(env, monkeypatch, a, result, SECOND_LOOP)
    for _ in range(LIMIT + 3):
        _read(env)
    assert _b1_after(env, floor, "approval_refused") == [("approval_refused", _mark(first))] * LIMIT
    assert node_run(env, a)["status"] == "review" and _review_status(result) != "approved"
    assert _wire(env, a)["intervention"] == {"kind": "system_error", "reason": "approval_refused"}
    assert [r["seq"] for r in assignments(env, a) if r["stage"] == "result_review"] == [1, 2]
