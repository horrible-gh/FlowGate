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

def test_a_review_none_card_whose_result_has_a_loop_approves_nothing(pre_feature_attempts, env, decided, monkeypatch):
    key, result, _token = _in_review(env, monkeypatch, REVIEWED)
    _passed_loop(env, result)
    _stored(env, key, "review_count", "0")                               # set to 'none' after its review started
    _lost_run_end(monkeypatch, env, key)
    _reads(env)
    assert _waits(env) == [(result, "review_result_unknown")] * LIMIT and _review_status(result) == "pending_review"
def test_a_read_that_fails_approves_nothing_and_is_not_tried_again(pre_feature_attempts, env, decided, monkeypatch):
    from flowgate.card_settings import legacy_adapter

    key, result, _token = _in_review(env, monkeypatch, REVIEWED)
    _passed_loop(env, result)

    def broken(doc_id):
        raise RuntimeError("loop read failed")

    monkeypatch.setattr(legacy_adapter, "review_loops", broken)
    _lost_run_end(monkeypatch, env, key)
    _reads(env)                                                          # a system error: tried again up to the limit
    assert _waits(env) == [(result, "review_result_unknown")] * LIMIT and _review_status(result) == "pending_review"
@pytest.mark.parametrize("ending", [{"end_reason": "timeout", "stop_code": "timeout"}, {"end_reason": "cancelled"}])
def test_a_result_whose_ai_run_did_not_end_on_its_own_is_never_reconciled(env, decided, monkeypatch, ending):
    key, result, _token = _in_review(env, monkeypatch, NO_REVIEW)
    _lost_run_end(monkeypatch, env, key, **ending)
    _current(env)
    assert _review_status(result) == "pending_review" and _waits(env) == [] and _authorities(env) == []
def test_every_existing_record_is_kept_and_only_added_to(env, decided, monkeypatch):
    key, result, _token = _in_review(env, monkeypatch, NO_REVIEW)
    before = _events(env)
    history = _sql(env["db"], "SELECT rejection_history FROM documents WHERE doc_id = ?", (result,))[0]["rejection_history"]
    _lost_run_end(monkeypatch, env, key)
    _current(env)
    after = _events(env)
    assert after[:len(before)] == before                                 # nothing changed, nothing removed
    assert _sql(env["db"], "SELECT rejection_history FROM documents WHERE doc_id = ?",
                (result,))[0]["rejection_history"] == history
