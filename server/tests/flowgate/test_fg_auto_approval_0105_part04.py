"""Independently selectable scenarios; shared fixtures remain in test_fg_auto_approval_0105.py."""
from __future__ import annotations

from test_fg_auto_approval_0105 import (
    FA,
    LIMIT,
    NO_REVIEW,
    REVIEWED,
    REVIEW_RUN,
    WORK_RUN,
    _active_exec,
    _approval_route_gone,
    _authorities,
    _current,
    _events,
    _in_review,
    _instruction_lost,
    _instruction_passed,
    _intervention,
    _loop_row,
    _loops,
    _lost_run_end,
    _node,
    _node_run,
    _non_admin,
    _passed_loop,
    _reads,
    _recorded_start_run,
    _refusals,
    _refused,
    _result_jobs,
    _review_starts,
    _review_status,
    _run_status,
    _sql,
    _stage,
    _stopped,
    _stored,
    _takes_its_result_stage_once,
    _verdict,
    _waits,
    _work_run_ended,
    card_outputs_port,
    db_path,
    decided,
    env,
    json,
    legacy_cards,
    legacy_setting,
    pre_feature_attempts,
    pytest,
    pytestmark,
)

def test_a_lost_run_end_whose_review_cannot_start_is_retried_up_to_the_limit(pre_feature_attempts, env, decided,
                                                                              monkeypatch):
    from flowgate.card_settings import legacy_adapter

    key, result, _token = _in_review(env, monkeypatch, (("review_count", 1), ("instruction", "omit")))
    monkeypatch.setattr(legacy_adapter, "default_reviewer", lambda project_id: None)
    calls = _recorded_start_run(monkeypatch)
    _lost_run_end(monkeypatch, env, key)
    for _ in range(LIMIT + 1):
        assert _node_run(env, key)["approval_wait_reason"] == "review_not_started"
    assert calls == [] and _waits(env) == [(result, "review_not_started")] * LIMIT
    assert _review_status(result) == "pending_review"
def test_a_review_still_working_is_never_approved_even_when_no_ai_is_told_working(env, decided, monkeypatch):
    from flowgate.card_settings import review_stage

    key, result, _token = _in_review(env, monkeypatch, REVIEWED)
    _loop_row(result, "rework")
    monkeypatch.setattr(review_stage, "ai_working", lambda *_a: False)  # ④-b: the two judgements disagree
    _lost_run_end(monkeypatch, env, key)
    _current(env)
    _current(env)
    assert _review_status(result) == "pending_review" and _waits(env) == [] and _authorities(env) == []
@pytest.mark.parametrize("stop_reason", ["review_count_exhausted", "review_verdict_hold", "retry_exhausted",
                                         "total_timeout", "restart_orphaned", "review_stalled"])
def test_a_lost_run_end_of_a_review_that_did_not_pass_records_it_once(pre_feature_attempts, env, decided, monkeypatch, stop_reason):
    key, result, _token = _in_review(env, monkeypatch, REVIEWED)
    _passed_loop(env, result, stop_reason)
    loops = _loops(env)
    calls = _recorded_start_run(monkeypatch)
    _lost_run_end(monkeypatch, env, key)
    _reads(env)                                                          # needs_intervention: recorded once, never again
    reason = f"review_not_passed:{stop_reason}"
    assert _intervention(env, key) == (False, {"kind": "needs_intervention", "reason": reason}, reason)
    assert _waits(env) == [(result, reason)]
    assert calls == [] and _loops(env) == loops and _review_status(result) == "pending_review"
@pytest.mark.parametrize("raw", ["{bad", '"1"', "5"])
def test_a_review_count_that_cannot_be_read_approves_nothing(pre_feature_attempts, env, decided, monkeypatch, raw):
    key, result, _token = _in_review(env, monkeypatch, REVIEWED)
    _stored(env, key, "review_count", raw)
    _lost_run_end(monkeypatch, env, key)
    _reads(env)
    assert _intervention(env, key)[1] == {"kind": "system_error", "reason": "review_result_unknown"}
    assert _waits(env) == [(result, "review_result_unknown")] * LIMIT and _review_status(result) == "pending_review"
@pytest.mark.parametrize("case", ["earlier_attempt", "earlier_revision"])
def test_a_pass_that_is_not_the_current_one_approves_nothing(pre_feature_attempts, env, decided, monkeypatch, case):
    key, result, _token = _in_review(env, monkeypatch, REVIEWED)
    _passed_loop(env, result)
    _verdict(monkeypatch, case != "earlier_revision")
    _lost_run_end(monkeypatch, env, key, review_exec=case != "earlier_attempt")
    _reads(env)
    assert _waits(env) == [(result, "review_result_unknown")] * LIMIT and _review_status(result) == "pending_review"
