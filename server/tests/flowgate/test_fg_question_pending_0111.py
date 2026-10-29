"""flowgate.v02.0111 T#2 (T-A1, NR flowgate.v02.0111.0003 §2 A-1) — an AI card run that asked questions and ended before its
document was (fully) submitted (``exited`` / ``question_pending``) waits for its answers and then goes on, once.

On the same real SQLite DB, routes and fixtures as ``test_fg_card_settings`` / ``test_fg_instruction_stage_0056`` (the
Graph engine, its execution ledger ``graph_attempt_runs``, the card settings / documents / approval ports, the run-end port,
the question wait and the run read are the real ones). Stand-ins: the AI invoke admission (a recorder, as those suites), the
asking AI run's finalized row (``ai_invoke_runs``, written as the finalize writes it) and its execution's ledger end (the
acquire's ``ai_run_id``, then ``ended``), the questions it asked (``finalize.open_questions_raised_by_run``) and the
responder's start (``ask_responder``) — the responder choice itself is 0110 T-D's (``test_fg_question_wait_responder``).

NR A-1 regression 1~9. Common assertions: while waiting the node has no ``dispatch_error_code`` (nor a wire ``error_code``)
and the run is not idle ``error``; after the answers exactly one execution request (a new ``graph_attempt_runs`` row and
one AI start); a settle after it never brings ``error`` back; no new attempt.
"""
from __future__ import annotations

import json

import pytest

from test_fg_card_approval_0056 import _current, _node_run, _resume, _run_id
from test_fg_card_settings import (  # noqa: F401 — fixtures used by name
    AI, FA, _run_one_card, _sql, _start, db_path, decided, env, setting,
)
from test_fg_card_settings_review import (
    INSTRUCTION_RUN, WORK_RUN, _admin_and_reviewer, _review_status, _start_ai, _through_instruction, _write_written,
)

ROOT = f"{FA}.0001-R"
RESPONDER_RUN = "air_card_responder"
QUESTION = {"id": 901, "anchor_doc_id": ROOT, "asker_kind": "ai", "responder_state": None,
            "asker_actual_provider_id": "aip_assignee"}


@pytest.fixture(autouse=True)
def card_approval_port():
    """The card approval's Graph port, registered as ``startup`` registers it."""
    from flowgate.card_approval import integration

    integration.register()
    yield
    integration.unregister()


@pytest.fixture()
def questions(monkeypatch):
    """The questions each AI run asked, still unanswered (``{ai run id: [item]}``); the responder starts recorded."""
    from flowgate.card_settings import legacy_adapter as legacy
    from modules.flow_gate.services.ai_invoke import finalize

    asked: dict[str, list[dict]] = {}
    state = {"asked": asked, "responders": [], "answered_by": {}}
    monkeypatch.setattr(finalize, "open_questions_raised_by_run",
                        lambda group_id, run_id: [dict(i) for i in asked.get(run_id, [])])
    monkeypatch.setattr(legacy, "independent_question_responder",
                        lambda project_id, asker, candidates: ("aip_review", "card_review_ai"))
    monkeypatch.setattr(legacy, "ask_responder",
                        lambda doc_id, item_id, issued_to, provider, source:
                        state["responders"].append((item_id, provider)) or {"run_id": RESPONDER_RUN})
    monkeypatch.setattr(legacy, "askers_answered_by", lambda run_id: set(state["answered_by"].get(run_id, ())))
    return state


# ── helpers ──────────────────────────────────────────────────────────────────

def _row(env, key):
    return _sql(env["db"], "SELECT status, result_doc_id, stage, instruction_doc_id, dispatch_error_code "
                           "FROM graph_node_runs WHERE node_key = ?", (key,))[0]


def _executions(env):
    return _sql(env["db"], "SELECT exec_id, status, ai_run_id, request_json FROM graph_attempt_runs "
                           "ORDER BY created_at, exec_id")


def _attempts(env, key):
    return _sql(env["db"], "SELECT attempt_id, status FROM graph_node_attempts WHERE node_key = ?", (key,))


def _tokens(env, key):
    return _sql(env["db"], "SELECT COUNT(*) AS n FROM graph_run_token_map WHERE node_key = ?", (key,))[0]["n"]


def _ai_run_finished(env, run_id, exec_id, *, stop_code=None, end_reason="exited", exit_code=0, doc_ref=ROOT):
    """The AI run's execution acquired (its ``ai_run_id``) and ended, and the finalize wrote its row."""
    _sql(env["db"], "UPDATE graph_attempt_runs SET status = 'ended', ai_run_id = ?, ended_at = '2026-10-29T00:00:00Z' "
                    "WHERE exec_id = ?", (run_id, exec_id))
    _sql(env["db"], "INSERT INTO ai_invoke_runs (run_id, group_id, project_id, doc_ref, mode, status, outcome, end_reason, "
                    "stop_code, exit_code, provider_id, issued_to, started_at, finished_at, created_at, updated_at) "
                    "VALUES (?, ?, 'flowgate', ?, 'single', 'finished', ?, ?, ?, ?, 'aip_assignee', 'u_kim', 't', 't', "
                    "'t', 't')",
         (run_id, FA, doc_ref, "complete" if stop_code is None else "none", end_reason, stop_code, exit_code))


def _ended(env, run_id, exec_id, **fields):
    """The run-end port after the AI run released its lease (``ai_run_end_hooks``), inside its execution's run-end
    hand-off as an owned end has it (``execution.finish`` -> ``begin_handoff``; ``handoff_end``, then the finalize's
    settle)."""
    from flowgate.card_settings import legacy_adapter as legacy
    from flowgate.graph_run import execution_store as ledger
    from modules.flow_gate.services import ai_run_end_hooks

    ledger.begin_handoff(exec_id, _run_id(env))
    try:
        ai_run_end_hooks.run_ended({"run_id": run_id, "group_id": FA, "doc_ref": ROOT, "action_scope": "new",
                                    "mode": "single", "issued_to": "u_kim", "provider_id": "aip_assignee",
                                    "outcome": "complete", "end_reason": "exited", "stop_code": None,
                                    "graph_exec_id": exec_id, **fields})
    finally:
        ledger.end_handoff(exec_id)
    legacy.settle_graph_run(FA)


def _asked_instruction(env, monkeypatch, questions, *, stop_code="question_pending", body=None, ask=True, settings=()):
    """An AI card (with ``settings``) whose instruction AI (an edit start into the line's empty document) asked a
    question (``ask``) and ended with ``stop_code`` after writing ``body`` (None: nothing). -> (node key, the instruction
    line's document, the stage's execution id)."""
    _admin_and_reviewer(env)
    key = _run_one_card(env, AI)
    for field, value in settings:
        setting(env, FA, key, field, value)
    _start(env)
    [job] = env["jobs"]
    env["jobs"].clear()
    assert (job["stage"], job["action_scope"]) == ("instruction", "edit")
    _start_ai(monkeypatch, job, INSTRUCTION_RUN)
    if body is not None:
        _write_written(job["target_doc_id"], body)
    if ask:
        questions["asked"][INSTRUCTION_RUN] = [dict(QUESTION)]
    _ai_run_finished(env, INSTRUCTION_RUN, job["graph_exec_id"], stop_code=stop_code, doc_ref=job["target_doc_id"])
    _ended(env, INSTRUCTION_RUN, job["graph_exec_id"], action_scope="edit", doc_ref=job["target_doc_id"],
           stop_code=stop_code, outcome="complete" if stop_code is None else "none")
    return key, job["target_doc_id"], job["graph_exec_id"]


def _asked_result(env, monkeypatch, questions, *, stop_code="question_pending", ask=True):
    """An AI card through its instruction stage whose result writer asked (``ask``) and ended with ``stop_code`` and
    no result. -> (node key, the result stage's execution id)."""
    _admin_and_reviewer(env)
    key = _run_one_card(env, AI)
    _start(env)
    _through_instruction(env, monkeypatch)
    [job] = env["jobs"]
    env["jobs"].clear()
    assert job["stage"] == "result"
    _start_ai(monkeypatch, job, WORK_RUN)
    if ask:
        questions["asked"][WORK_RUN] = [dict(QUESTION, id=902)]
    _ai_run_finished(env, WORK_RUN, job["graph_exec_id"], stop_code=stop_code)
    _ended(env, WORK_RUN, job["graph_exec_id"], stop_code=stop_code, outcome="none")
    return key, job["graph_exec_id"]


def _assert_waiting(env, key):
    """No dispatch error on the node (nor on its wire) and the run not idle ``error``, a run read included."""
    assert _row(env, key)["dispatch_error_code"] is None
    run = _current(env)                         # the run read: reconcile + settle
    wire = next(n for n in run["node_runs"] if n["node_key"] == key)
    assert (wire["dispatch"] or {}).get("error_code") is None
    assert run["idle_reason"] != "error"
    return run


def _answer(questions, asker, how):
    """The asker's questions answered: a person's answer (``on_question_answered``), the responder AI's run end
    (``follow``), or both — the same answers told twice."""
    from flowgate.card_settings import question_wait

    questions["asked"][asker] = []
    if how in ("human", "both"):
        question_wait.on_question_answered({"author_kind": "human", "group_id": FA, "asker_ai_run_id": asker})
    if how in ("ai", "both"):
        questions["answered_by"][RESPONDER_RUN] = {asker}
        question_wait.follow({"run_id": RESPONDER_RUN, "group_id": FA})


def _after_answers(env, key, before_execs, attempts):
    """Exactly one execution requested and one AI start; no new attempt; a settle brings no ``error`` back."""
    from flowgate.card_settings import legacy_adapter as legacy

    [job] = env["jobs"]
    execs = _executions(env)
    assert len(execs) == before_execs + 1
    assert execs[-1]["status"] == "requested" and execs[-1]["exec_id"] == job["graph_exec_id"]
    assert _attempts(env, key) == attempts
    legacy.settle_graph_run(FA)
    run = _current(env)
    assert run["status"] == "running" and run["idle_reason"] is None
    assert _row(env, key)["dispatch_error_code"] is None
    return job


# ── 1, 2, 7: instruction stage, nothing written (question_pending) ──────────

@pytest.mark.parametrize("how", ["human", "ai", "both"])
def test_an_instruction_stage_that_asked_before_writing_waits_and_writes_again_once(env, decided, monkeypatch,
                                                                                   questions, how):
    """1 (a person's answer), 2 (the responder AI's answer), 7 (both: the same answers told twice) — the 0083 form."""
    key, line_doc, first_exec = _asked_instruction(env, monkeypatch, questions)
    assert questions["responders"] == [(901, "aip_review")]            # its question went to the card's responder
    row = _row(env, key)
    assert (row["status"], row["stage"]) == ("open", "instruction")
    run = _assert_waiting(env, key)
    assert run["status"] == "idle"
    attempts, execs = _attempts(env, key), len(_executions(env))
    assert env["jobs"] == []

    _answer(questions, INSTRUCTION_RUN, how)

    job = _after_answers(env, key, execs, attempts)
    assert (job["stage"], job["action_scope"], job["target_doc_id"]) == ("instruction", "edit", line_doc)
    assert job["instruction_retry"] is True and job["result_doc_type"] == "T"
    assert json.loads(_executions(env)[-1]["request_json"])["job"]["stage"] == "instruction"
    assert _row(env, key)["stage"] == "instruction"
    # the same answers told again (a late follow-up): nothing more
    _answer(questions, INSTRUCTION_RUN, "both")
    assert len(env["jobs"]) == 1 and len(_executions(env)) == execs + 1


# ── 3, 4: instruction stage, written (a clean end that asked — the 0084 form) ──

@pytest.mark.parametrize("how", ["human", "ai"])
def test_a_written_instruction_that_asked_is_approved_and_its_result_stage_starts_once(env, decided, monkeypatch,
                                                                                      questions, how):
    key, line_doc, _exec = _asked_instruction(env, monkeypatch, questions, stop_code=None,
                                              body="## 작업\n\n지시 AI가 쓴 지시서\n")
    assert questions["responders"] == [(901, "aip_review")]
    assert _review_status(line_doc) != "approved"                      # its questions first
    run = _assert_waiting(env, key)
    attempts, execs = _attempts(env, key), len(_executions(env))

    _answer(questions, INSTRUCTION_RUN, how)

    job = _after_answers(env, key, execs, attempts)
    assert job["stage"] == "result" and job["ref_doc_ids"] == [line_doc]   # no instruction stage again
    assert _review_status(line_doc) == "approved"
    row = _row(env, key)
    assert (row["stage"], row["instruction_doc_id"]) == ("result", line_doc)
    _answer(questions, INSTRUCTION_RUN, "both")                         # 7: once
    assert len(env["jobs"]) == 1 and len(_executions(env)) == execs + 1
    assert run["status"] == "idle"


def test_a_written_instruction_answered_while_the_run_is_judged_idle_still_starts_its_result_stage(
        env, decided, monkeypatch, questions):
    """The A-2 race at the new path: the run settles idle between the wake and the step — ``person_hold`` + ``idle_ok``
    still take the step (the result stage's request takes the run back to running)."""
    from flowgate.card_settings import legacy_adapter as legacy

    key, line_doc, _exec = _asked_instruction(env, monkeypatch, questions, stop_code=None,
                                              body="## 작업\n\n지시서\n")
    attempts, execs = _attempts(env, key), len(_executions(env))
    woken = legacy.wake_graph_run

    def wake_then_settle(flow_id):
        ok = woken(flow_id)
        legacy.settle_graph_run(flow_id)            # a run read's settle in between: nothing works -> idle
        assert _sql(env["db"], "SELECT status FROM graph_runs WHERE group_id = ?", (FA,))[0]["status"] == "idle"
        return ok

    monkeypatch.setattr(legacy, "wake_graph_run", wake_then_settle)
    _answer(questions, INSTRUCTION_RUN, "human")
    job = _after_answers(env, key, execs, attempts)
    assert job["stage"] == "result" and _review_status(line_doc) == "approved"
    # 0111 T#3: a refusal's run event carries its node only in its payload — read the run's events by cause
    causes = [r["cause"] for r in _sql(env["db"], "SELECT cause FROM graph_run_events")]
    assert "attempt_inactive" not in causes


# ── 5: instruction stage, partly written and question_pending ───────────────

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


# ── 6: result stage, nothing written (question_pending) ─────────────────────

@pytest.mark.parametrize("how", ["human", "ai", "both"])
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


# ── 8: a Resume while waiting ───────────────────────────────────────────────

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


# ── 9: the existing paths ────────────────────────────────────────────────────

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


def test_a_result_writer_with_no_result_and_no_question_is_still_an_error_a_resume_retries(env, decided, monkeypatch,
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
