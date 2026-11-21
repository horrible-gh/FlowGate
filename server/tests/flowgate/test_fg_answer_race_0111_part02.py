"""Independently selectable scenarios; shared fixtures remain in test_fg_answer_race_0111.py."""
from __future__ import annotations

from test_fg_answer_race_0111 import (
    AI,
    FA,
    INSTRUCTION_RUN,
    QUESTION,
    REVIEW_RUN,
    WORK_RUN,
    _after_answers,
    _ai_run_finished,
    _answer,
    _answered_writer_reviewed,
    _ask,
    _asked_instruction,
    _asked_result,
    _attempts,
    _counted,
    _current,
    _executions,
    _in_review,
    _kinds,
    _ledgered_review_start,
    _node_status,
    _once_and_unrefused,
    _passed_review,
    _refusals,
    _refused_result_stage,
    _resume,
    _review_ended,
    _review_status,
    _row,
    _run_ended,
    _run_id,
    _run_one_card,
    _run_status,
    _sql,
    _start,
    card_outputs_port,
    db_path,
    decided,
    env,
    json,
    legacy_cards,
    pytest,
    pytestmark,
    question_index,
    questions,
    racing_wake,
    setting,
)

def test_an_answered_result_writer_raced_idle_is_approved_once(env, decided, monkeypatch, questions, racing_wake):
    """``_after_work`` with no review: the result's automatic approval (``result_approval``)."""
    from flowgate.card_settings import legacy_adapter as legacy

    key, result, _token = _in_review(env, monkeypatch)
    env["jobs"].clear()
    latest = _executions(env)[-1]["exec_id"]
    _ai_run_finished(env, WORK_RUN, latest)
    _ask(questions, WORK_RUN, 905)
    _run_ended(WORK_RUN, graph_exec_id=latest)
    legacy.settle_graph_run(FA)
    assert questions["responders"] == [(905, "aip_review")]
    assert (_node_status(env, key), _review_status(result)) == ("review", "pending_review")
    assert _run_status(env) == "idle"

    _answer(questions, WORK_RUN, "both")

    _once_and_unrefused(env, racing_wake)
    assert (_node_status(env, key), _review_status(result)) == ("done", "approved")
    _answer(questions, WORK_RUN, "both")
    assert _node_status(env, key) == "done" and _refusals(env) == []
def test_an_answered_result_writer_raced_idle_starts_its_review_once(env, decided, monkeypatch, questions, racing_wake):
    """``_after_work`` with a review: the review loop's start (``review_start``), an explicit request on an idle run —
    its durable execution requested once; the same answers told again while it works start nothing."""
    from flowgate.card_settings import legacy_adapter as legacy

    key, result, starts, _loop_exec = _answered_writer_reviewed(env, monkeypatch, questions, racing_wake)
    execs = len(_executions(env))
    _answer(questions, WORK_RUN, "both")
    assert len(starts) == 1 and len(_executions(env)) == execs and _refusals(env) == []
    assert legacy.approval_waits(_run_id(env), key, result) == []
@pytest.mark.parametrize("stop_reason", ["retry_exhausted", "review_verdict_hold"])
def test_answers_told_again_after_the_writer_s_review_ended_without_passing_start_no_second_review(
        env, decided, monkeypatch, questions, racing_wake, stop_reason):
    """Review rejection P2: the review the answers started ended without passing (its loop row stopped, its execution
    ended) and the node is still in review; the writer's answers told again late — a person's, then both — find its
    execution no longer the attempt's latest (``legacy.result_continuation_due``): no wake, no second loop, nothing
    recorded beyond the review's own end."""
    from flowgate.card_settings import legacy_adapter as legacy
    from flowgate.card_settings import question_wait

    key, result, starts, loop_exec = _answered_writer_reviewed(env, monkeypatch, questions, racing_wake)
    _review_ended(env, questions, result, loop_exec, stop_reason)
    run_id = _run_id(env)
    assert (_node_status(env, key), _review_status(result)) == ("review", "pending_review")
    assert legacy.review_loop_active(result) is False and legacy.node_ai_run_active(FA, key) is False
    waits = legacy.approval_waits(run_id, key, result)
    assert waits == ["review_not_passed:" + stop_reason]
    assert legacy.result_continuation_due(run_id, key, WORK_RUN) is False
    assert question_wait._waiting(FA, legacy.ai_run_record(WORK_RUN)) is None
    execs, wakes = len(_executions(env)), len(racing_wake)

    _answer(questions, WORK_RUN, "human")
    _answer(questions, WORK_RUN, "both")

    assert len(starts) == 1 and len(_executions(env)) == execs and len(racing_wake) == wakes
    assert legacy.approval_waits(run_id, key, result) == waits and _refusals(env) == []
    assert (_node_status(env, key), _review_status(result)) == ("review", "pending_review")
def test_a_writer_s_step_judged_under_its_hold_takes_nothing_once_its_review_was_requested(env, decided, monkeypatch,
                                                                                          questions, racing_wake):
    """Review rejection P2, under the hold: ``_after_work`` (``answered``) called again directly for the writer after its
    review was requested and ended — past ``_waiting`` — starts no second review and records nothing
    (``_answered_result_hold``)."""
    from flowgate.card_settings import legacy_adapter as legacy
    from flowgate.card_settings import review_stage

    key, result, starts, loop_exec = _answered_writer_reviewed(env, monkeypatch, questions, racing_wake)
    _review_ended(env, questions, result, loop_exec, "retry_exhausted")
    waits, execs = legacy.approval_waits(_run_id(env), key, result), len(_executions(env))
    questions["asked"][WORK_RUN] = []

    review_stage._after_work(FA, legacy.ai_run_record(WORK_RUN), answered=True)

    assert len(starts) == 1 and len(_executions(env)) == execs and _refusals(env) == []
    assert legacy.approval_waits(_run_id(env), key, result) == waits
def test_an_answered_result_writer_with_no_result_raced_idle_is_retried_once(env, decided, monkeypatch, questions,
                                                                            racing_wake):
    key, _exec = _asked_result(env, monkeypatch, questions)
    attempts, execs = _attempts(env, key), len(_executions(env))
    assert _run_status(env) == "idle"

    _answer(questions, WORK_RUN, "both")

    _once_and_unrefused(env, racing_wake)
    job = _after_answers(env, key, execs, attempts)
    assert job["stage"] == "result"
    _answer(questions, WORK_RUN, "both")
    assert len(env["jobs"]) == 1 and len(_executions(env)) == execs + 1 and _refusals(env) == []
