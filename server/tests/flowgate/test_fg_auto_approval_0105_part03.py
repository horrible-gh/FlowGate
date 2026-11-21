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

def test_a_review_that_cannot_start_is_recorded_and_never_approved(pre_feature_attempts, env, decided, monkeypatch):
    from flowgate.card_settings import legacy_adapter

    key, result, _token = _in_review(env, monkeypatch, (("review_count", 1), ("instruction", "omit")))
    monkeypatch.setattr(legacy_adapter, "default_reviewer", lambda project_id: None)   # no reviewer can be chosen
    calls = _recorded_start_run(monkeypatch)
    _work_run_ended()
    _lost_run_end(monkeypatch, env, key)
    for _ in range(LIMIT - 1):                                           # ④-a: read again: retried (0119 T-2)
        assert _intervention(env, key)[1:] == ({"kind": "system_error", "reason": "review_not_started"},
                                               "review_not_started")
    _stored(env, key, "review_count", "0")                               # set to 'none' after the limit: still nothing
    _current(env)
    assert calls == [] and _review_status(result) == "pending_review"
    assert _waits(env) == [(result, "review_not_started")] * LIMIT and _loops(env) == []
@pytest.mark.parametrize("stored", [None, 0])
@pytest.mark.parametrize("admin", [True, False])
def test_a_lost_run_end_of_a_review_none_card_is_approved_once_by_a_run_read(env, decided, monkeypatch, stored, admin):
    key, result, _token = _in_review(env, monkeypatch, NO_REVIEW)
    if stored is not None:
        _stored(env, key, "review_count", json.dumps(stored))
    if not admin:
        _non_admin(monkeypatch)
    _lost_run_end(monkeypatch, env, key)
    assert _node_run(env, key)["status"] == "done"
    assert (_node(env, key), _review_status(result)) == (("done", result), "approved")
    before = _events(env)
    _current(env)
    assert _events(env) == before                                        # read again: no approval, no event
    assert _authorities(env) == [(result, "system", "u_kim")] and _waits(env) == []
def test_a_lost_approval_of_a_passed_review_is_made_once_and_the_loop_is_kept(pre_feature_attempts, env, decided, monkeypatch):
    key, result, _token = _in_review(env, monkeypatch, REVIEWED)
    _passed_loop(env, result)
    _verdict(monkeypatch, True)
    loops = _loops(env)
    _lost_run_end(monkeypatch, env, key)
    assert _node_run(env, key)["status"] == "done" and _review_status(result) == "approved"
    before = _events(env)
    _current(env)
    assert _events(env) == before and _loops(env) == loops               # round_no / attempts_used unchanged
    assert _authorities(env) == [(result, "system", "u_kim")]
def test_a_lost_run_end_before_the_review_starts_the_review_once_and_approves_nothing(env, decided, monkeypatch):
    key, result, _token = _in_review(env, monkeypatch, REVIEWED)
    calls = _review_starts(monkeypatch, env, result)
    _lost_run_end(monkeypatch, env, key)
    for _ in range(3):
        shown = _node_run(env, key)
        assert shown["status"] == "review" and _review_status(result) == "pending_review"
    assert len(calls) == 1 and calls[0]["doc_ref"] == result and calls[0]["action_scope"] == "review"
    assert _authorities(env) == [] and _waits(env) == []
