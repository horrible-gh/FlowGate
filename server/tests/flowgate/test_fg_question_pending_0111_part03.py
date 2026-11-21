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

@pytest.mark.parametrize("how", ["human", "ai", "both", "chat"])
def test_a_result_writer_that_asked_waits_without_error_and_is_retried_once(env, decided, monkeypatch, questions, how):
    from modules.flow_gate.services import graph_execution_hooks

    key, _exec = _asked_result(env, monkeypatch, questions)
    assert questions["responders"] == [(902, "aip_review")]
    row = _sql(env["db"], "SELECT * FROM graph_node_runs WHERE node_key = ?", (key,))[0]
    assert graph_execution_hooks.call("write_failure", row) == "question_pending"
    _assert_waiting(env, key)
    attempts, execs = _attempts(env, key), len(_executions(env))

    _answer(questions, WORK_RUN, how)

    job = _after_answers(env, key, execs, attempts)
    assert job["stage"] == "result"
    row = _sql(env["db"], "SELECT * FROM graph_node_runs WHERE node_key = ?", (key,))[0]
    assert graph_execution_hooks.call("write_failure", row) is None    # a new request hides the old end
def test_a_result_writer_whose_question_read_fails_keeps_waiting(env, decided, monkeypatch, questions):
    from modules.flow_gate.services import graph_execution_hooks
    from modules.flow_gate.services.ai_invoke import finalize

    key, _exec = _asked_result(env, monkeypatch, questions)

    def unreadable(group_id, run_id):
        raise RuntimeError("questions unreadable")

    monkeypatch.setattr(finalize, "open_questions_raised_by_run", unreadable)
    row = _sql(env["db"], "SELECT * FROM graph_node_runs WHERE node_key = ?", (key,))[0]
    assert graph_execution_hooks.call("write_failure", row) == "question_pending"
@pytest.mark.parametrize("stage", ["instruction", "result"])
def test_a_resume_while_the_questions_wait_dispatches_nothing(env, decided, monkeypatch, questions, stage):
    if stage == "instruction":
        key, _doc, _exec = _asked_instruction(env, monkeypatch, questions)
    else:
        key, _exec = _asked_result(env, monkeypatch, questions)
    assert _current(env)["status"] == "idle"
    tokens, execs = _tokens(env, key), len(_executions(env))
    _resume(env)
    assert env["jobs"] == [] and _tokens(env, key) == tokens and len(_executions(env)) == execs
    _assert_waiting(env, key)
    assert "redispatched" not in [r["cause"] for r in _sql(
        env["db"], "SELECT cause FROM graph_run_events WHERE node_key = ?", (key,))]
@pytest.mark.parametrize("stop_code, end_reason", [
    ("providers_exhausted", "all_providers_failed"), ("cancelled", "cancelled"), ("timeout", "timeout"),
    ("question_pending", "timeout"),                    # asking, but not an own end: still a failure
])
def test_an_instruction_stage_that_stopped_otherwise_still_fails(env, decided, monkeypatch, questions, stop_code,
                                                                 end_reason):
    _admin_and_reviewer(env)
    key = _run_one_card(env, AI)
    _start(env)
    [job] = env["jobs"]
    env["jobs"].clear()
    _start_ai(monkeypatch, job, INSTRUCTION_RUN)
    questions["asked"][INSTRUCTION_RUN] = [dict(QUESTION)]
    _ai_run_finished(env, INSTRUCTION_RUN, job["graph_exec_id"], stop_code=stop_code, end_reason=end_reason)
    _ended(env, INSTRUCTION_RUN, job["graph_exec_id"], action_scope="edit", doc_ref=job["target_doc_id"],
           stop_code=stop_code, end_reason=end_reason, outcome="none")
    assert _row(env, key)["dispatch_error_code"] == "ai_invoke_failed"
    assert _current(env)["idle_reason"] == "error"
    assert questions["responders"] == [] and env["jobs"] == []
