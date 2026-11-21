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

def test_a_partly_written_instruction_that_ended_asking_is_written_again_never_approved(env, decided, monkeypatch,
                                                                                       questions):
    key, line_doc, _exec = _asked_instruction(env, monkeypatch, questions, body="## 작업\n\n절반만 쓴 지시서\n")
    _assert_waiting(env, key)
    attempts, execs = _attempts(env, key), len(_executions(env))

    _answer(questions, INSTRUCTION_RUN, "human")

    job = _after_answers(env, key, execs, attempts)
    assert (job["stage"], job["action_scope"], job["target_doc_id"]) == ("instruction", "edit", line_doc)
    assert _review_status(line_doc) != "approved"                      # what it wrote never read the answers
    assert _row(env, key)["stage"] == "instruction"
@pytest.mark.parametrize("review", [0, 1])
def test_an_earlier_asker_answered_again_after_the_stage_was_written_again_continues_nothing(env, decided, monkeypatch,
                                                                                           questions, review):
    """5 / 7 across two runs of the stage (review rej_01M62NE5CKPYH6MW): ① run A ended on its own with no instruction
    and asked — answered, run B writes the stage again on the same attempt; ② B writes part of the instruction, asks in
    turn and ends asking; ③ A's answers told again (a person's and the responder's): A is clean and the line's document
    has B's partial body now, yet nothing is approved, reviewed (``instruction_review_count`` 1) or requested, nothing is
    woken or refused, and B's question still waits — the continuation's own guard refuses it too; ④ B answered: the stage
    is written again exactly once, never approved."""
    from flowgate.card_settings import legacy_adapter as legacy
    from flowgate.card_settings import question_wait, review_stage

    second = "air_card_instruction_again"
    reviews: list = []
    monkeypatch.setattr(legacy, "start_review_loop",
                        lambda *args, **kw: reviews.append(args) or {"run_id": "air_card_instruction_review"})
    key, line_doc, _exec = _asked_instruction(env, monkeypatch, questions, stop_code=None,
                                              settings=(("instruction_review_count", review),) if review else ())
    attempts = _attempts(env, key)
    # ① A answered: B requested on the same attempt
    _answer(questions, INSTRUCTION_RUN, "human")
    [job_b] = env["jobs"]
    env["jobs"].clear()
    assert (job_b["stage"], job_b["target_doc_id"], job_b["instruction_retry"]) == ("instruction", line_doc, True)
    # ② B writes part of it, asks and ends asking
    _start_ai(monkeypatch, job_b, second)
    _write_written(line_doc, "## 작업\n\n절반만 쓴 지시서\n")
    questions["asked"][second] = [dict(QUESTION, id=903)]
    _ai_run_finished(env, second, job_b["graph_exec_id"], stop_code="question_pending", doc_ref=line_doc)
    _ended(env, second, job_b["graph_exec_id"], action_scope="edit", doc_ref=line_doc, stop_code="question_pending",
           outcome="none")
    assert questions["responders"] == [(901, "aip_review"), (903, "aip_review")]
    questions["asked"][second][0]["responder_state"] = "running"       # as ``dispatch_answer_run`` marks it
    run = _assert_waiting(env, key)
    assert run["status"] == "idle" and env["jobs"] == []
    execs = len(_executions(env))
    events = _sql(env["db"], "SELECT COUNT(*) AS n FROM graph_run_events")[0]["n"]

    def nothing_more():
        assert env["jobs"] == [] and len(_executions(env)) == execs and _attempts(env, key) == attempts
        assert _review_status(line_doc) != "approved" and reviews == []
        assert legacy.approval_waits(_run_id(env), key, line_doc) == []
        row = _row(env, key)
        assert (row["status"], row["stage"], row["result_doc_id"]) == ("open", "instruction", None)
        assert _sql(env["db"], "SELECT COUNT(*) AS n FROM graph_run_events")[0]["n"] == events   # not woken, nor refused
        assert _sql(env["db"], "SELECT status FROM graph_runs WHERE group_id = ?", (FA,))[0]["status"] == "idle"
        assert questions["asked"][second] and len(questions["responders"]) == 2              # B's question still waits
        assert question_wait._waiting(FA, legacy.ai_run_record(INSTRUCTION_RUN)) is None
        assert question_wait._waiting(FA, legacy.ai_run_record(second))[0] == "instruction"

    # ③ A's answers told again
    _answer(questions, INSTRUCTION_RUN, "both")
    nothing_more()
    node, row = legacy.instruction_node_of_ai_run(FA, INSTRUCTION_RUN)     # A still maps to the node's stage
    review_stage.continue_instruction_after_answers(FA, legacy.ai_run_record(INSTRUCTION_RUN), node, row)
    nothing_more()
    _assert_waiting(env, key)

    # ④ B answered: once
    _answer(questions, second, "human")
    job = _after_answers(env, key, execs, attempts)
    assert (job["stage"], job["action_scope"], job["target_doc_id"]) == ("instruction", "edit", line_doc)
    assert job["instruction_retry"] is True
    assert _review_status(line_doc) != "approved" and reviews == []
    _answer(questions, INSTRUCTION_RUN, "both")
    _answer(questions, second, "both")
    assert len(env["jobs"]) == 1 and len(_executions(env)) == execs + 1
def test_an_instruction_stage_whose_questions_are_answered_at_its_end_goes_on_at_once(env, decided, monkeypatch,
                                                                                    questions):
    """``hold`` False at the asking end (answered before the run ended): written again at once, no failure."""
    key, line_doc, _exec = _asked_instruction(env, monkeypatch, questions, ask=False)
    assert questions["responders"] == []
    [job] = env["jobs"]
    assert (job["stage"], job["action_scope"], job["target_doc_id"]) == ("instruction", "edit", line_doc)
    assert _row(env, key)["dispatch_error_code"] is None
