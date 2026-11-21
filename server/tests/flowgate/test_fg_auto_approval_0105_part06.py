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

@pytest.mark.parametrize("stored", [None, 0])
def test_a_lost_instruction_end_on_an_idle_run_is_approved_and_takes_its_result_stage_once(env, decided, monkeypatch,
                                                                                            stored):
    settings = () if stored is None else (("instruction_review_count", stored),)
    key, instruction = _instruction_lost(env, monkeypatch, settings)
    _current(env)
    _takes_its_result_stage_once(env, key, instruction)
    assert _authorities(env) == [(instruction, "system", "u_kim")]
def test_a_lost_approval_of_a_passed_instruction_review_on_an_idle_run_takes_its_result_stage_once(pre_feature_attempts, env, decided,
                                                                                                   monkeypatch):
    key, instruction = _instruction_lost(env, monkeypatch, (("instruction_review_count", 1),))
    _instruction_passed(env, instruction)
    _verdict(monkeypatch, True)
    loops = _loops(env)
    _current(env)
    _takes_its_result_stage_once(env, key, instruction)
    assert _authorities(env) == [(instruction, "system", "u_kim")] and _loops(env) == loops
def test_an_approved_instruction_whose_result_stage_was_lost_takes_it_once_on_an_idle_run(env, decided, monkeypatch):
    from modules.flow_gate.db import documents as db_documents

    key, instruction = _instruction_lost(env, monkeypatch)
    db_documents.update(instruction, {"doc_review_status": "approved"})   # approved; the result stage's start lost
    _current(env)
    _takes_its_result_stage_once(env, key, instruction)
    assert _authorities(env) == []                                       # approved once, not again
def test_a_lost_instruction_end_that_needs_its_review_starts_it_once_on_an_idle_run(pre_feature_attempts, env, decided, monkeypatch):
    key, instruction = _instruction_lost(env, monkeypatch, (("instruction_review_count", 1),))
    calls = _review_starts(monkeypatch, env, instruction)
    for _ in range(3):
        _current(env)
    assert len(calls) == 1 and calls[0]["doc_ref"] == instruction and calls[0]["action_scope"] == "review"
    assert _run_status(env)[0] == "running"                              # the explicit request woke the idle run
    assert _review_status(instruction) != "approved" and _stage(env, key) == ("open", "instruction")
    assert _refusals(env) == [] and _waits(env) == [] and _authorities(env) == [] and _result_jobs(env) == []
