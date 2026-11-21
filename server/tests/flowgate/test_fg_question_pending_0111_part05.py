"""Independently selectable scenarios; shared fixtures remain in test_fg_question_pending_0111.py."""
from __future__ import annotations

from test_fg_question_pending_0111 import (
    AI,
    CHAT_RUN,
    FA,
    INSTRUCTION_RUN,
    QUESTION,
    RESPONDER_RUN,
    ROOT,
    WORK_RUN,
    _admin_and_reviewer,
    _after_answers,
    _ai_run_finished,
    _answer,
    _asked_instruction,
    _asked_result,
    _assert_waiting,
    _attempts,
    _current,
    _ended,
    _executions,
    _node_run,
    _resume,
    _review_status,
    _row,
    _run_id,
    _run_one_card,
    _sql,
    _start,
    _start_ai,
    _through_instruction,
    _tokens,
    _write_written,
    card_outputs_port,
    db_path,
    decided,
    env,
    json,
    legacy_cards,
    pre_feature_attempts,
    pytest,
    pytestmark,
    questions,
    setting,
)

def test_an_answered_result_writer_that_ended_asking_reads_as_no_result(env, decided, monkeypatch, questions):
    """All its questions answered: the same as ``no_result`` (the answers' retry, or an explicit command's)."""
    from modules.flow_gate.services import graph_execution_hooks

    key, _exec = _asked_result(env, monkeypatch, questions)
    questions["asked"][WORK_RUN] = []
    row = _sql(env["db"], "SELECT * FROM graph_node_runs WHERE node_key = ?", (key,))[0]
    assert graph_execution_hooks.call("write_failure", row) == "no_result"
def test_the_continuation_is_the_new_entry_point_never_the_ended_run_judged_again():
    """``question_wait._answered`` goes on through ``continue_instruction_after_answers`` (source contract)."""
    import inspect

    from flowgate.card_settings import question_wait

    source = inspect.getsource(question_wait._answered)
    assert "continue_instruction_after_answers" in source and "_after_instruction" not in source
    assert question_wait.ended_without_result({"end_reason": "exited", "stop_code": "question_pending"})
    assert question_wait.ended_waiting({"end_reason": "exited", "stop_code": "question_pending"})
    assert not question_wait.ended_waiting({"end_reason": "timeout", "stop_code": "question_pending"})
@pytest.mark.parametrize("stage", ["instruction", "result"])
def test_exhausted_responder_leaves_card_and_run_in_question_wait_without_redispatch(
        env, decided, monkeypatch, questions, stage):
    """T-A3: responder exhaustion is an item failure, never a card/run error.

    The Q-answer engine's exhaustion is covered by test_q_responder_startup_0111;
    here the real Graph run read/settle and repeated end/Resume see its failed item.
    """
    from flowgate.card_settings import question_wait

    if stage == "instruction":
        key, _doc, _exec = _asked_instruction(env, monkeypatch, questions)
        asker = INSTRUCTION_RUN
    else:
        key, _exec = _asked_result(env, monkeypatch, questions)
        asker = WORK_RUN
    [item] = questions["asked"][asker]
    item.update(responder_state="failed", responder_error_code="provider_failed", answer_count=0)
    attempts, execs = _attempts(env, key), len(_executions(env))
    for _ in range(3):
        question_wait.follow({"run_id": RESPONDER_RUN, "group_id": FA})
        _resume(env)
        run = _assert_waiting(env, key)
        assert run["status"] == "idle" and run["idle_reason"] != "error"
    assert questions["responders"] == [(item["id"], "aip_review")]
    assert _attempts(env, key) == attempts and len(_executions(env)) == execs
    assert env["jobs"] == [] and item["answer_count"] == 0
@pytest.mark.parametrize("stage", ["instruction", "result"])
def test_unclaimed_busy_responder_retries_then_answers_resume_once(
        env, decided, monkeypatch, questions, stage):
    """A deferred response reaches the existing answer/resume path on the real Graph DB."""
    from flowgate.card_settings import question_wait, legacy_adapter as legacy
    if stage == "instruction":
        key, _doc, _exec = _asked_instruction(env, monkeypatch, questions)
        asker = INSTRUCTION_RUN
    else:
        key, _exec = _asked_result(env, monkeypatch, questions)
        asker = WORK_RUN
    [item] = questions["asked"][asker]
    item.update(asker_ai_run_id=asker, responder_state="failed", responder_error_code="run_in_progress",
                responder_run_id=None, responder_attempts=0)
    questions["responders"].clear()
    monkeypatch.setattr(legacy, "open_ai_questions", lambda _: [item])
    attempts, execs = _attempts(env, key), len(_executions(env))
    with monkeypatch.context() as busy:
        busy.setattr(legacy, "flow_ai_busy", lambda _: True)
        question_wait.follow({"run_id": "other-run", "group_id": FA})
    assert questions["responders"] == []
    question_wait.follow({"run_id": "other-run", "group_id": FA})
    assert questions["responders"] == [(item["id"], "aip_review")]
    item.update(responder_state="dispatched", responder_run_id=RESPONDER_RUN, responder_attempts=1)
    question_wait.follow({"run_id": "other-run", "group_id": FA})
    assert len(questions["responders"]) == 1
    assert len(_executions(env)) == execs
    monkeypatch.setattr(legacy, "open_ai_questions", lambda _: [])
    _answer(questions, asker, "ai")
    assert len(_executions(env)) == execs + 1
    _answer(questions, asker, "both")
    assert len(_executions(env)) == execs + 1
    assert _attempts(env, key) == attempts
