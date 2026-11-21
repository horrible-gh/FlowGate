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

def test_an_ai_run_that_timed_out_needs_intervention_and_tells_why(env, decided, monkeypatch):
    key, result, _token = _in_review(env, monkeypatch, NO_REVIEW)
    _work_run_ended(end_reason="timeout", stop_code="timeout")
    assert _intervention(env, key) == (False, {"kind": "needs_intervention", "reason": "run_not_clean:timeout"},
                                       "run_not_clean:timeout")
    _reads(env)
    assert _review_status(result) == "pending_review" and _waits(env) == [(result, "run_not_clean:timeout")]
def test_a_refused_completion_is_a_system_error_retried_up_to_the_limit(env, decided, monkeypatch):
    """(T-1): the automatic completion takes no v0.1 review transition; its refusal is recorded
    (``approval_refused``). the earlier implementation (T-2): a system error — each run read tries the path again, recorded each time, until the
    limit; never a person's approval."""
    key, result, _token = _in_review(env, monkeypatch, NO_REVIEW)
    _refused(monkeypatch)
    _work_run_ended()
    assert _intervention(env, key) == (False, {"kind": "system_error", "reason": "approval_refused"},
                                       "approval_refused")
    _reads(env, LIMIT + 2)                                               # past the limit: nothing more is tried
    assert _waits(env) == [(result, "approval_refused")] * LIMIT and _authorities(env) == []
    _approval_route_gone(env, key)                                       # T-3: the route is gone
    assert (_node(env, key)[0], _review_status(result)) == ("review", "pending_review")
def test_a_system_error_whose_retry_succeeds_completes_the_card(env, decided, monkeypatch):
    from flowgate.card_settings import legacy_adapter

    key, result, _token = _in_review(env, monkeypatch, NO_REVIEW)
    real = legacy_adapter.completion_gates
    _refused(monkeypatch)
    _work_run_ended()
    assert _waits(env) == [(result, "approval_refused")]
    monkeypatch.setattr(legacy_adapter, "completion_gates", real)         # the cause went away
    _current(env)                                                        # the reconciliation's retry
    assert (_node(env, key), _review_status(result)) == (("done", result), "approved")
    assert _intervention(env, key) == (False, None, None) and _waits(env) == [(result, "approval_refused")]
def test_no_issuer_approves_nothing(pre_feature_attempts, env, decided, monkeypatch):
    key, result, _token = _in_review(env, monkeypatch, NO_REVIEW)
    _work_run_ended(issued_to="")
    assert _intervention(env, key)[1:] == ({"kind": "system_error", "reason": "issuer_missing"}, "issuer_missing")
    assert _review_status(result) == "pending_review"
