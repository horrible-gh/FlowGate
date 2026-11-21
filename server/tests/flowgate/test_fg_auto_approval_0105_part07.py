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

@pytest.mark.parametrize("failure", ["no_reviewer", "start_refused"])
def test_a_lost_instruction_review_that_cannot_start_on_an_idle_run_is_retried_up_to_the_limit(pre_feature_attempts, env, decided, monkeypatch, failure):
    from flowgate.card_settings import legacy_adapter
    from modules.flow_gate.services import ai_invoke_service

    key, instruction = _instruction_lost(env, monkeypatch, (("instruction_review_count", 1),))
    starts = []
    if failure == "no_reviewer":
        monkeypatch.setattr(legacy_adapter, "default_reviewer", lambda project_id: None)

    def refuse(**kw):
        starts.append(kw)
        raise RuntimeError("admission refused")

    monkeypatch.setattr(ai_invoke_service, "start_run", refuse)
    _reads(env)
    assert len(starts) == (0 if failure == "no_reviewer" else LIMIT)    # T-2: a system error, retried
    assert _waits(env) == [(instruction, "review_not_started")] * LIMIT and _refusals(env) == []
    assert _review_status(instruction) != "approved" and _stage(env, key) == ("open", "instruction")
    assert _result_jobs(env) == []
@pytest.mark.parametrize("closed", ["stopped", "time_machine"])
@pytest.mark.parametrize("review", [0, 1])
def test_a_stopped_or_rewinding_idle_run_reconciles_no_instruction_stage(env, decided, monkeypatch, closed, review):
    from flowgate.graph_time_machine import operation

    key, instruction = _instruction_lost(env, monkeypatch, (("instruction_review_count", review),))
    calls = _review_starts(monkeypatch, env, instruction)
    if closed == "stopped":
        _stopped(env)
    else:
        monkeypatch.setattr(operation, "busy", lambda flow_id: True)
    before = _events(env)
    for _ in range(2):
        _current(env)
    assert calls == [] and _result_jobs(env) == [] and _review_status(instruction) != "approved"
    assert _stage(env, key) == ("open", "instruction") and _events(env)[len(before):] == []
@pytest.mark.parametrize("review", [0, 1])
def test_a_stop_that_commits_before_the_hold_refuses_the_reconciled_instruction_stage(env, decided, monkeypatch, review):
    """The Stop commits between the read's own judgement and the Stop gate's hold: the hold judges the run again and
    refuses (recorded) — no approval, no result stage, no review."""
    from flowgate.card_settings import legacy_adapter

    key, instruction = _instruction_lost(env, monkeypatch, (("instruction_review_count", review),))
    calls = _review_starts(monkeypatch, env, instruction)
    _stopped(env)
    monkeypatch.setattr(legacy_adapter, "person_stage_open", lambda flow_id, run_row: True)   # the read's stale view
    _current(env)
    assert calls == [] and _result_jobs(env) == [] and _review_status(instruction) != "approved"
    assert _stage(env, key) == ("open", "instruction") and _authorities(env) == []
    assert "run_stopped" in _refusals(env)
@pytest.mark.parametrize("review", [0, 1])
def test_an_instruction_run_of_an_earlier_attempt_is_never_reconciled(env, decided, monkeypatch, review):
    key, instruction = _instruction_lost(env, monkeypatch, (("instruction_review_count", review),),
                                         current_attempt=False)
    calls = _review_starts(monkeypatch, env, instruction)
    before = _events(env)
    _current(env)
    assert calls == [] and _result_jobs(env) == [] and _review_status(instruction) != "approved"
    assert _stage(env, key) == ("open", "instruction") and _events(env)[len(before):] == []
def test_a_card_its_review_left_is_never_approved_by_a_person_even_an_admin(pre_feature_attempts, env, decided, monkeypatch):
    key, result, _token = _in_review(env, monkeypatch, REVIEWED)
    _passed_loop(env, result, "review_count_exhausted")                  # formerly H1: a person decided
    _lost_run_end(monkeypatch, env, key)
    reason = "review_not_passed:review_count_exhausted"
    assert _intervention(env, key) == (False, {"kind": "needs_intervention", "reason": reason}, reason)
    _approval_route_gone(env, key)                                       # an admin too (0119 T-3: no route)
    assert (_node(env, key)[0], _review_status(result)) == ("review", "pending_review") and _authorities(env) == []
