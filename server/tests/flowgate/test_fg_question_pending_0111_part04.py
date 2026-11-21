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

def test_a_clean_instruction_stage_with_no_question_is_approved_as_before(env, decided, monkeypatch, questions):
    key, line_doc, _exec = _asked_instruction(env, monkeypatch, questions, stop_code=None, body="## 작업\n\n본문\n",
                                              ask=False)
    [job] = env["jobs"]
    assert job["stage"] == "result" and _review_status(line_doc) == "approved"
    assert questions["responders"] == []
def test_a_clean_instruction_stage_that_wrote_nothing_and_asked_nothing_still_fails(env, decided, monkeypatch,
                                                                                   questions):
    key, _doc, _exec = _asked_instruction(env, monkeypatch, questions, stop_code=None, ask=False)
    assert _row(env, key)["dispatch_error_code"] == "ai_invoke_failed"
    assert env["jobs"] == []
def test_a_result_writer_with_no_result_and_no_question_is_still_an_error_a_resume_retries(pre_feature_attempts, env, decided, monkeypatch,
                                                                                           questions):
    """The 0063 ``no_result``: idle ``error`` with the wire's ``error_code``, and an explicit command dispatches again."""
    from modules.flow_gate.services import graph_execution_hooks

    key, _exec = _asked_result(env, monkeypatch, questions, stop_code=None, ask=False)
    row = _sql(env["db"], "SELECT * FROM graph_node_runs WHERE node_key = ?", (key,))[0]
    assert graph_execution_hooks.call("write_failure", row) == "no_result"
    assert questions["responders"] == []
    run = _current(env)
    assert run["idle_reason"] == "error"
    assert (_node_run(env, key)["dispatch"] or {}).get("error_code") == "no_result"
    tokens = _tokens(env, key)
    _resume(env)
    assert _tokens(env, key) > tokens                       # redispatched, as before
