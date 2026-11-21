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

def test_retry_result_stage_takes_an_idle_run_only_when_asked_to(env, decided, monkeypatch, questions):
    """``node_dispatch.retry_result_stage`` keeps refusing an idle run by default (recorded); ``idle_ok`` — under
    ``person_hold`` — requests the retry once, and a second call requests nothing."""
    from flowgate.card_settings import legacy_adapter as legacy
    from modules.flow_gate.graph_workflow.services import node_dispatch

    key, _exec = _asked_result(env, monkeypatch, questions)
    questions["asked"][WORK_RUN] = []
    run_id, attempt = _run_id(env), legacy.active_attempt_id(_run_id(env), key)
    assert _run_status(env) == "idle"
    execs = len(_executions(env))
    assert node_dispatch.retry_result_stage(FA, run_id, key, "u_kim", "flowgate", attempt_id=attempt) is False
    assert _refusals(env) == [("result_retry", "run_idle")] and env["jobs"] == []
    jobs: list = []
    with legacy.person_hold(FA, run_id, key, attempt, "result_retry") as owned:
        assert owned
        assert node_dispatch.retry_result_stage(FA, run_id, key, "u_kim", "flowgate", attempt_id=attempt,
                                                idle_ok=True, defer=jobs) is True
    for job in jobs:
        job()
    assert len(env["jobs"]) == 1 and len(_executions(env)) == execs + 1 and _run_status(env) == "running"
    assert node_dispatch.retry_result_stage(FA, run_id, key, "u_kim", "flowgate", attempt_id=attempt,
                                            idle_ok=True) is False
    assert len(_executions(env)) == execs + 1
@pytest.mark.parametrize("read", ["run_read", "resume"])
def test_a4_the_run_read_right_after_a_refused_result_stage_starts_it_once(env, decided, monkeypatch, questions, read):
    """NR A-4: each silent return of ``_reconcile_instruction`` asserted on the 0084 state (``_handoff_pending``,
    ``node_ai_run_active``, ``stage_ai_run``, ``attempt_of_ai_run``, ``open_questions_of_ai_run``,
    ``written_instruction``), then the read (or a Resume) takes the approved instruction's result stage once; a second
    read takes nothing more. Before the earlier implementation it returned at ``stage_ai_run`` None: the run's durable row has no
    ``action_scope``."""
    from flowgate.card_settings import legacy_adapter as legacy
    from flowgate.card_settings import review_stage

    key, line_doc = _refused_result_stage(env, monkeypatch, questions)
    run_id = _run_id(env)
    node, row = legacy.node_of_key(FA, key), legacy.node_run(run_id, key)
    assert review_stage._ai_card(FA, key) is not None
    assert review_stage._handoff_pending(FA, key) is False
    assert legacy.node_ai_run_active(FA, key) is False
    # the A-4 cause: its durable row tells no ``action_scope``, so ``stage_ai_run`` found no run before the earlier implementation
    assert legacy.ai_run_record(INSTRUCTION_RUN).get("action_scope") is None
    work = legacy.stage_ai_run(FA, run_id, key)
    assert work is not None and work["run_id"] == INSTRUCTION_RUN and work["action_scope"] == "edit"
    assert review_stage.ended_cleanly(work)
    attempt = legacy.active_attempt_id(run_id, key)
    assert attempt is not None
    assert legacy.attempt_of_ai_run(work) == attempt
    assert legacy.open_questions_of_ai_run(FA, INSTRUCTION_RUN) == []
    assert review_stage.written_instruction(FA, node, row) == line_doc
    assert legacy.person_stage_open(FA, legacy.active_run(FA))
    execs = len(_executions(env))

    if read == "run_read":
        _current(env)
    else:
        _resume(env)

    [job] = env["jobs"]
    assert job["stage"] == "result" and job["ref_doc_ids"] == [f"{FA}.0001-R", line_doc]
    assert len(_executions(env)) == execs + 1
    assert _row(env, key)["stage"] == "result" and _run_status(env) == "running"
    assert _refusals(env) == [("instruction_result_stage", "run_idle")]               # only seq 11's
    _current(env)
    assert len(env["jobs"]) == 1 and len(_executions(env)) == execs + 1
def test_a4_a_result_writer_s_durable_row_is_found_by_its_minted_token_s_action_scope(env, decided, monkeypatch):
    """The same cause on the result's reconciliation (``result_ai_run``): the writer's durable row has no
    ``action_scope``; the token its AI start minted (``edit``) tells it."""
    from flowgate.card_settings import legacy_adapter as legacy

    key, result, _token = _in_review(env, monkeypatch)
    _ai_run_finished(env, WORK_RUN, _executions(env)[-1]["exec_id"])
    assert legacy.ai_run_record(WORK_RUN).get("action_scope") is None
    found = legacy.result_ai_run(FA, _run_id(env), key, result)
    assert found is not None and (found["run_id"], found["action_scope"]) == (WORK_RUN, "edit")
    assert legacy.stage_ai_run(FA, _run_id(env), key)["run_id"] == WORK_RUN
@pytest.mark.parametrize("read", ["run_read", "resume"])
def test_a_passed_result_review_whose_questions_wait_is_not_reconciled_before_its_answer(env, decided, monkeypatch,
                                                                                       questions, read):
    """The writer ended cleanly with no question and its review started; the review passed (its loop row, its execution
    of the attempt, a ``pass`` record) asking a question still unanswered. The run read / Resume reconciliation
    (``_reconcile_result``, found since the A-4 fix) approves nothing, starts nothing and records no approval wait; the
    answer approves once, and a read after it adds nothing."""
    from flowgate.card_settings import legacy_adapter as legacy
    from flowgate.card_settings import review_stage

    starts = _ledgered_review_start(monkeypatch)
    key, result, _token = _in_review(env, monkeypatch, (("review_count", 2), ("review_ai", "aip_review")))
    env["jobs"].clear()
    writer = _executions(env)[-1]["exec_id"]
    _ai_run_finished(env, WORK_RUN, writer)
    _run_ended(WORK_RUN, graph_exec_id=writer)
    [loop_exec] = starts
    assert loop_exec is not None
    approvals = _counted(monkeypatch, legacy, "approve", 1)
    _review_ended(env, questions, result, loop_exec, "review_passed", item=907)
    assert questions["responders"][-1] == (907, "aip_review")
    run_id = _run_id(env)
    attempt = legacy.active_attempt_id(run_id, key)
    # every other condition of the reconciliation's approval holds: only the review's question keeps it
    assert legacy.result_ai_run(FA, run_id, key, result)["run_id"] == WORK_RUN
    assert legacy.open_questions_of_ai_run(FA, WORK_RUN) == []
    latest = legacy.review_loops(result)[0]
    assert (latest["run_id"], latest["stop_reason"]) == (REVIEW_RUN, "review_passed")
    assert review_stage._loop_of_attempt(latest, attempt) and legacy.submission_passed(result)
    assert legacy.open_questions_of_ai_run(FA, REVIEW_RUN) != []
    execs = len(_executions(env))

    if read == "run_read":
        _current(env)
    else:
        _resume(env)
    _current(env)

    assert approvals == [] and len(starts) == 1 and len(_executions(env)) == execs and env["jobs"] == []
    assert (_node_status(env, key), _review_status(result)) == ("review", "pending_review")
    assert legacy.approval_waits(run_id, key, result) == []

    _answer(questions, REVIEW_RUN, "both")

    assert approvals == [result]
    assert (_node_status(env, key), _review_status(result)) == ("done", "approved")
    _current(env)
    assert approvals == [result] and len(starts) == 1 and _refusals(env) == []
@pytest.mark.parametrize("read", ["run_read", "resume"])
def test_a_passed_instruction_review_whose_questions_wait_is_not_reconciled_before_its_answer(env, decided, monkeypatch,
                                                                                            questions, read):
    """The instruction writer ended cleanly with no question; its review (``instruction_review_count`` 1) passed asking a
    question still unanswered. The run read / Resume reconciliation (``_reconcile_instruction``, found since the A-4 fix)
    neither approves the instruction nor starts the result stage, and records no approval wait; the answer does both
    once (one execution request), and a read after it adds nothing."""
    from flowgate.card_settings import legacy_adapter as legacy
    from flowgate.card_settings import review_stage

    starts = _ledgered_review_start(monkeypatch)
    key, line_doc, _exec = _asked_instruction(env, monkeypatch, questions, stop_code=None, body="## 작업\n\n지시서\n",
                                              ask=False, settings=(("instruction_review_count", 1), ("instruction_review_ai", "aip_review")))
    [loop_exec] = starts
    assert loop_exec is not None and env["jobs"] == []
    approvals = _counted(monkeypatch, review_stage, "approve_instruction", 2)
    _review_ended(env, questions, line_doc, loop_exec, "review_passed", item=908)
    assert questions["responders"][-1] == (908, "aip_review")
    run_id = _run_id(env)
    attempt = legacy.active_attempt_id(run_id, key)
    node, row = legacy.node_of_key(FA, key), legacy.node_run(run_id, key)
    # every other condition of the reconciliation holds: only the review's question keeps it
    work = legacy.stage_ai_run(FA, run_id, key)
    assert work["run_id"] == INSTRUCTION_RUN and legacy.attempt_of_ai_run(work) == attempt
    assert legacy.open_questions_of_ai_run(FA, INSTRUCTION_RUN) == []
    assert review_stage.written_instruction(FA, node, row) == line_doc
    latest = legacy.review_loops(line_doc)[0]
    assert review_stage._loop_of_attempt(latest, attempt) and legacy.submission_passed(line_doc)
    assert legacy.open_questions_of_ai_run(FA, REVIEW_RUN) != []
    attempts, execs = _attempts(env, key), len(_executions(env))

    if read == "run_read":
        _current(env)
    else:
        _resume(env)
    _current(env)

    assert approvals == [] and env["jobs"] == [] and len(_executions(env)) == execs
    assert _review_status(line_doc) != "approved" and _row(env, key)["stage"] == "instruction"
    assert legacy.approval_waits(run_id, key, line_doc) == []

    _answer(questions, REVIEW_RUN, "both")

    job = _after_answers(env, key, execs, attempts)
    assert job["stage"] == "result" and job["ref_doc_ids"] == [f"{FA}.0001-R", line_doc]
    assert approvals == [line_doc] and _review_status(line_doc) == "approved"
    _current(env)
    assert len(env["jobs"]) == 1 and len(_executions(env)) == execs + 1 and approvals == [line_doc]
    assert _refusals(env) == []
