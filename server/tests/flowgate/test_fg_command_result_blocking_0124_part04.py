"""Independently selectable scenarios; shared fixtures remain in test_fg_command_result_blocking_0124.py."""
from __future__ import annotations

from test_fg_command_result_blocking_0124 import (
    A,
    B,
    EXCLUDED,
    FA,
    PERSON_WAITS,
    RELEASE,
    REVIEW_RUN,
    SECOND_LOOP,
    X,
    _h,
    _hold,
    _latest,
    _loop_end,
    _loop_row,
    _mixed,
    _mixed_spent,
    _read,
    _reviewed_ai_result,
    _rows,
    _run,
    _shape,
    _spent,
    _sql,
    _wire,
    assignments,
    command,
    db_path,
    decided,
    edge,
    env,
    events,
    fork_join,
    ledger,
    memo_routes,
    node,
    node_run,
    one,
    put_draft,
    pytest,
    read_result,
    record_starts,
    roles,
    starts,
    stored,
    wired,
    write_line,
)

def test_s7_a_refused_re_request_start_is_the_command_s_failure_with_its_real_code(
        env, decided, monkeypatch, starts, fork_join):
    from modules.flow_gate.services import ai_invoke_service

    a, _a2, _a3, _result, _first = _spent(env, monkeypatch, starts)

    class Refused(RuntimeError):
        code = "ai_disabled"

    def refused(**kwargs):
        raise Refused("the reviewer is disabled")

    monkeypatch.setattr(ai_invoke_service, "start_run", refused)
    command(env, "step")
    r = _latest(env)
    print("S7 state =", r["state"], "failure =", r["failure"], "blocking =", r["blocking"])  # measured (see the TR)
    assert r["state"] == "failed"
    assert r["failure"]["node_key"] == a and r["failure"]["code"] == "stage_failed"
    assert r["failure"]["detail"] == "start_refused:ai_disabled"
def test_s8_reading_the_command_result_writes_nothing(env, decided, monkeypatch, starts, fork_join):
    a, _a2, _a3, result, first = _spent(env, monkeypatch, starts)
    EXCLUDED["review_stalled"](env, a, result, first)
    command(env, "step")
    before = _rows(env)
    for _ in range(3):
        assert len(_latest(env)["blocking"]) == 1
    assert _rows(env) == before
def test_s9_a_failed_read_leaves_the_card_out_or_its_release_unsaid_and_the_read_goes_on(
        env, decided, monkeypatch, starts, fork_join):
    from flowgate.card_settings import stage_assignment as sa
    from flowgate.graph_run import legacy_adapter as engine

    a, _a2, _a3, result, first = _spent(env, monkeypatch, starts)
    EXCLUDED["review_stalled"](env, a, result, first)
    command(env, "step")
    whole = _latest(env)
    assert whole["blocking"][0]["release"] == "memo_rereview"

    def broken(*_a, **_k):
        raise RuntimeError("a read failed")

    with monkeypatch.context() as m:
        m.setattr(sa, "current_review_assignment", broken)
        r = _latest(env)
        [b] = r["blocking"]
        assert b["node_key"] == a and b["release"] is None                                     # the release unsaid
        assert {k: r[k] for k in ("state", "nodes", "counts", "idle_reason")} == {k: whole[k] for k in ("state", "nodes", "counts", "idle_reason")}
    with monkeypatch.context() as m:
        m.setattr(engine, "intervention", broken)
        r = _latest(env)
        assert r["blocking"] == []                                                             # the card left out
        assert {k: r[k] for k in ("state", "nodes", "counts", "idle_reason")} == {k: whole[k] for k in ("state", "nodes", "counts", "idle_reason")}
