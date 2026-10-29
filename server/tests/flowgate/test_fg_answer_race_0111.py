"""flowgate.v02.0111 T#3 (T-A2, NR flowgate.v02.0111.0003 §2 A-2 / A-4) — a card step that goes on once its questions are
answered is taken under ``legacy.person_hold`` with ``idle_ok`` in every phase (``question_wait._answered``: instruction,
review, work, no_result), so a run judged idle between the wake and the step never refuses it; and the run read's
reconciliation of an approved instruction whose result stage was refused (the 0084 state after seq 11).

On the same real SQLite DB, routes and fixtures as ``test_fg_question_pending_0111`` (T#2). The race is injected in order
(``racing_wake``): the wake (``legacy.wake_graph_run``) is followed at once by the run's settle
(``run_service.settle_if_needed`` through ``legacy.settle_graph_run``, as a run read's), which is asserted to have judged
the run idle before the step runs — 0084 seq 8 -> 9.

Common assertions: exactly one step (one execution request and one AI start, or one approval), no ``attempt_inactive`` run
event (read by its cause: a refusal's event carries its node only in its payload), and the same answers told twice (a
person's answer and the responder's end) give that one step only.

Review rejection (rej_01M62WZFGSR6PG31): a result writer's review is started through the real execution request
(``execution.request_loop``: a durable ``graph_attempt_runs`` loop row, ``_ledgered_review_start``) — the same answers told
twice, and told again late after that review ended without passing (its loop row stopped, its execution ended), start no
second review (P2); and a passed review whose own questions still wait (its loop row, its execution, a ``pass`` review
record, an unanswered question) is neither approved nor followed by the run read / Resume reconciliation before the
answer, nor recorded as an approval wait, and goes on exactly once after it (P1).
"""
from __future__ import annotations

import json

import pytest

from test_fg_card_approval_0056 import _current, _resume, _run_id
from test_fg_card_settings import (  # noqa: F401 — fixtures used by name
    AI, FA, _run_one_card, _sql, _start, db_path, decided, env, setting,
)
from test_fg_card_settings_review import (
    INSTRUCTION_RUN, REVIEW_RUN, WORK_RUN, _in_review, _review_status, _run_ended,
)
from test_fg_question_pending_0111 import (  # noqa: F401 — fixtures used by name
    QUESTION, _after_answers, _ai_run_finished, _answer, _asked_instruction, _asked_result, _attempts, _executions,
    _row, card_approval_port, questions,
)


@pytest.fixture()
def racing_wake(env, monkeypatch):
    """0084 seq 8 -> 9: the wake, then at once a settle that judges the run idle (nothing works yet). -> the
    (woken, run status after the settle) of each wake."""
    from flowgate.card_settings import legacy_adapter as legacy

    woken = legacy.wake_graph_run
    seen: list = []

    def wake_then_settle(flow_id):
        ok = woken(flow_id)
        legacy.settle_graph_run(flow_id)
        seen.append((ok, _run_status(env)))
        return ok

    monkeypatch.setattr(legacy, "wake_graph_run", wake_then_settle)
    return seen


# ── helpers ──────────────────────────────────────────────────────────────────

def _run_status(env):
    return _sql(env["db"], "SELECT status FROM graph_runs WHERE group_id = ?", (FA,))[0]["status"]


def _refusals(env):
    """(operation, reason) of every ``attempt_inactive`` run event of the Flow's run."""
    rows = _sql(env["db"], "SELECT payload_json FROM graph_run_events WHERE cause = 'attempt_inactive' "
                           "ORDER BY event_seq")
    return [(p.get("operation"), p.get("reason")) for p in (json.loads(r["payload_json"]) for r in rows)]


def _node_status(env, key):
    return _sql(env["db"], "SELECT status FROM graph_node_runs WHERE node_key = ?", (key,))[0]["status"]


def _passed_review(env, questions, doc_id):
    """The review loop ``REVIEW_RUN`` of ``doc_id`` passed after asking a question (its loop row and run row), then its
    run end (the run-end port, then the finalize's settle): the approval waits for the answer, its question put to the
    card's responder."""
    from flowgate.card_settings import legacy_adapter as legacy
    from modules.flow_gate.db import ai_invoke_document_review_loops as db_loops
    from modules.flow_gate.services import ai_run_end_hooks

    db_loops.insert({"run_id": REVIEW_RUN, "group_id": FA, "doc_ref": doc_id, "review_count": 1,
                     "reviewer_provider_id": "aip_review", "review_criteria": "document_type_default",
                     "rework_provider_id": "aip_assignee", "rework_timeout_sec": 3600,
                     "failure_restart_max_attempts": 0, "total_timeout_sec": 14400, "review_baseline_id": 0,
                     "baseline_revision_no": 0, "starts_with_rework": 0, "started_at": "t", "deadline_at": "t",
                     "current_stage": "review"})
    _sql(env["db"], "UPDATE ai_invoke_document_review_loops SET current_stage = 'stopped', "
                    "stop_reason = 'review_passed' WHERE run_id = ?", (REVIEW_RUN,))
    _ai_run_finished(env, REVIEW_RUN, None, doc_ref=doc_id)
    _ask(questions, REVIEW_RUN, 904)
    ai_run_end_hooks.run_ended({"run_id": REVIEW_RUN, "group_id": FA, "doc_ref": doc_id, "action_scope": "review",
                                "mode": "single", "issued_to": "u_kim",
                                "document_review_loop": {"current_stage": "stopped", "stop_reason": "review_passed"}})
    legacy.settle_graph_run(FA)
    assert questions["responders"] and questions["responders"][-1] == (904, "aip_review")


def _ask(questions, asker, item):
    questions["asked"][asker] = [dict(QUESTION, id=item)]


def _ledgered_review_start(monkeypatch):
    """The card's review loop start (``legacy.start_review_loop``) as far as its durable execution request: the real
    ``execution.request_loop`` (the attempt's loop row in ``graph_attempt_runs``, an idle run taken back to running under
    an explicit request), the AI invoke start not run; the scheduler's replay of that requested execution
    (``execution.replay``, ``graph_exec_id``) requests nothing new. -> the execution id of each start."""
    from flowgate.card_settings import legacy_adapter as legacy
    from flowgate.graph_run import execution

    starts: list = []

    def start(project_id, flow_id, doc_id, issued_to, loop, basis_note, node_key=None, graph_exec_id=None,
              attempt_id=None):
        if graph_exec_id:
            return {"run_id": REVIEW_RUN}       # the replay of a requested execution: its launch, not a new request
        exec_id = execution.request_loop(project_id, flow_id, doc_id, issued_to, loop, basis_note(None), node_key,
                                         None, attempt_id=attempt_id)
        starts.append(exec_id)
        return {"run_id": REVIEW_RUN} if exec_id else None

    monkeypatch.setattr(legacy, "start_review_loop", start)
    return starts


def _kinds(env):
    return [(r["kind"], r["status"]) for r in _sql(env["db"], "SELECT kind, status FROM graph_attempt_runs "
                                                              "ORDER BY created_at, exec_id")]


def _review_ended(env, questions, doc_id, exec_id, stop_reason, *, item=None):
    """The review loop ``REVIEW_RUN`` of ``doc_id`` (execution ``exec_id``) ended ``stop_reason``: its loop row, its
    execution ended with its run id, its finalized row, a ``pass`` review record when it passed, the question it asked
    (``item``), then its run end (the run-end port, then the finalize's settle)."""
    from flowgate.card_settings import legacy_adapter as legacy
    from modules.flow_gate.db import ai_invoke_document_review_loops as db_loops
    from modules.flow_gate.db import document_reviews
    from modules.flow_gate.db import documents as db_documents
    from modules.flow_gate.services import ai_run_end_hooks

    db_loops.insert({"run_id": REVIEW_RUN, "group_id": FA, "doc_ref": doc_id, "review_count": 1,
                     "reviewer_provider_id": "aip_review", "review_criteria": "document_type_default",
                     "rework_provider_id": "aip_assignee", "rework_timeout_sec": 3600,
                     "failure_restart_max_attempts": 0, "total_timeout_sec": 14400, "review_baseline_id": 0,
                     "baseline_revision_no": 0, "starts_with_rework": 0, "started_at": "t", "deadline_at": "t",
                     "current_stage": "review"})
    _sql(env["db"], "UPDATE ai_invoke_document_review_loops SET current_stage = 'stopped', stop_reason = ?, "
                    "stop_detail = ? WHERE run_id = ?",
         (stop_reason, None if stop_reason == "review_passed" else "ended without passing", REVIEW_RUN))
    _ai_run_finished(env, REVIEW_RUN, exec_id, doc_ref=doc_id)
    if stop_reason == "review_passed":
        revision = int((db_documents.get_by_id(doc_id) or {}).get("revision_no") or 0)
        document_reviews.insert_review(doc_id, revision, "u_review", "pass", "[]", None, "2026-10-29T00:00:00+00:00")
    if item is not None:
        _ask(questions, REVIEW_RUN, item)
    ai_run_end_hooks.run_ended({"run_id": REVIEW_RUN, "group_id": FA, "doc_ref": doc_id, "action_scope": "review",
                                "mode": "single", "issued_to": "u_kim", "graph_exec_id": exec_id,
                                "document_review_loop": {"current_stage": "stopped", "stop_reason": stop_reason}})
    legacy.settle_graph_run(FA)


def _counted(monkeypatch, module, name, at):
    """Every call of ``module.name`` (its argument ``at``: the document id), the real one still run."""
    real, seen = getattr(module, name), []

    def counted(*args, **kw):
        seen.append(args[at])
        return real(*args, **kw)

    monkeypatch.setattr(module, name, counted)
    return seen


def _once_and_unrefused(env, racing_wake):
    """The race was injected (the run judged idle right after the wake) and nothing was refused."""
    assert racing_wake and racing_wake[0] == (True, "idle")
    assert _refusals(env) == []


# ── instruction phase (T#2's continuation, kept) ─────────────────────────────

def test_an_answered_written_instruction_raced_idle_starts_its_result_stage_once(env, decided, monkeypatch, questions,
                                                                                racing_wake):
    key, line_doc, _exec = _asked_instruction(env, monkeypatch, questions, stop_code=None, body="## 작업\n\n지시서\n")
    attempts, execs = _attempts(env, key), len(_executions(env))

    _answer(questions, INSTRUCTION_RUN, "both")

    _once_and_unrefused(env, racing_wake)
    job = _after_answers(env, key, execs, attempts)
    assert job["stage"] == "result" and job["ref_doc_ids"] == [line_doc]
    assert _review_status(line_doc) == "approved"
    _answer(questions, INSTRUCTION_RUN, "both")
    assert len(env["jobs"]) == 1 and len(_executions(env)) == execs + 1 and _refusals(env) == []


def test_an_answered_unwritten_instruction_raced_idle_is_written_again_once(env, decided, monkeypatch, questions,
                                                                           racing_wake):
    key, line_doc, _exec = _asked_instruction(env, monkeypatch, questions)
    attempts, execs = _attempts(env, key), len(_executions(env))

    _answer(questions, INSTRUCTION_RUN, "both")

    _once_and_unrefused(env, racing_wake)
    job = _after_answers(env, key, execs, attempts)
    assert (job["stage"], job["target_doc_id"], job["instruction_retry"]) == ("instruction", line_doc, True)
    _answer(questions, INSTRUCTION_RUN, "both")
    assert len(env["jobs"]) == 1 and len(_executions(env)) == execs + 1 and _refusals(env) == []


# ── review phase ─────────────────────────────────────────────────────────────

def test_an_answered_passed_instruction_review_raced_idle_starts_its_result_stage_once(env, decided, monkeypatch,
                                                                                      questions, racing_wake):
    """``_after_review`` of an instruction: its approval and the result stage (``instruction_review_approval``)."""
    from flowgate.card_settings import legacy_adapter as legacy

    reviews: list = []
    monkeypatch.setattr(legacy, "start_review_loop",
                        lambda *args, **kw: reviews.append(args) or {"run_id": REVIEW_RUN})
    key, line_doc, _exec = _asked_instruction(env, monkeypatch, questions, stop_code=None, body="## 작업\n\n지시서\n",
                                              ask=False, settings=(("instruction_review_count", 1),))
    assert len(reviews) == 1 and env["jobs"] == []                     # its review first
    _passed_review(env, questions, line_doc)
    assert _review_status(line_doc) != "approved" and _run_status(env) == "idle"
    attempts, execs = _attempts(env, key), len(_executions(env))

    _answer(questions, REVIEW_RUN, "both")

    _once_and_unrefused(env, racing_wake)
    job = _after_answers(env, key, execs, attempts)
    assert job["stage"] == "result" and job["ref_doc_ids"] == [line_doc]
    assert _review_status(line_doc) == "approved"
    _answer(questions, REVIEW_RUN, "both")
    assert len(env["jobs"]) == 1 and len(_executions(env)) == execs + 1 and _refusals(env) == []


def test_an_answered_passed_result_review_raced_idle_is_approved_once(env, decided, monkeypatch, questions, racing_wake):
    """``_after_review`` of a result: its automatic approval (``review_approval``)."""
    key, result, _token = _in_review(env, monkeypatch, (("review_count", 2), ("review_ai", "aip_review")))
    env["jobs"].clear()
    _passed_review(env, questions, result)
    assert (_node_status(env, key), _review_status(result)) == ("review", "pending_review")
    assert _run_status(env) == "idle"

    _answer(questions, REVIEW_RUN, "both")

    _once_and_unrefused(env, racing_wake)
    assert (_node_status(env, key), _review_status(result)) == ("done", "approved")
    _answer(questions, REVIEW_RUN, "both")
    assert _node_status(env, key) == "done" and _refusals(env) == []


# ── work phase ───────────────────────────────────────────────────────────────

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


def _answered_writer_reviewed(env, monkeypatch, questions, racing_wake):
    """A result writer that asked (``WORK_RUN``, question 906) answered twice (a person's answer and the responder's end):
    its review loop requested once through the execution ledger on the run judged idle. -> (node key, result, the
    review starts, the review's execution id)."""
    from flowgate.card_settings import legacy_adapter as legacy

    starts = _ledgered_review_start(monkeypatch)
    key, result, _token = _in_review(env, monkeypatch, (("review_count", 2), ("review_ai", "aip_review")))
    env["jobs"].clear()
    writer = _executions(env)[-1]["exec_id"]
    _ai_run_finished(env, WORK_RUN, writer)
    _ask(questions, WORK_RUN, 906)
    _run_ended(WORK_RUN, graph_exec_id=writer)
    legacy.settle_graph_run(FA)
    assert starts == [] and _run_status(env) == "idle"
    assert legacy.result_continuation_due(_run_id(env), key, WORK_RUN) is True
    execs = len(_executions(env))

    _answer(questions, WORK_RUN, "both")

    _once_and_unrefused(env, racing_wake)
    assert len(racing_wake) == 1                                          # the responder's end found it under way
    [loop_exec] = starts
    assert loop_exec is not None and len(_executions(env)) == execs + 1
    assert _kinds(env)[-1] == ("review", "requested") and _run_status(env) == "running"
    assert legacy.approval_waits(_run_id(env), key, result) == []
    return key, result, starts, loop_exec


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


# ── no_result phase (``retry_result_stage``) ─────────────────────────────────

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


# ── A-4: the run read right after the result stage was refused ───────────────

def _refused_result_stage(env, monkeypatch, questions):
    """The 0084 state after seq 11, made by the continuation as it was before T#2 (``_answered`` -> ``_after_instruction``):
    the wake (seq 8), its approval's hold judged on the running run, the settle that judges it idle inside that hold (seq
    9), the instruction's approval (seq 10) and the result stage refused on the idle run (seq 11, ``result_stage`` /
    ``run_idle``). -> (node key, the instruction)."""
    from flowgate.card_settings import legacy_adapter as legacy
    from flowgate.card_settings import review_stage

    key, line_doc, _exec = _asked_instruction(env, monkeypatch, questions, stop_code=None, body="## 작업\n\n지시서\n")
    approve = review_stage.approve_instruction

    def settled_then_approved(*args):
        legacy.settle_graph_run(FA)
        assert _run_status(env) == "idle"
        return approve(*args)

    questions["asked"][INSTRUCTION_RUN] = []
    monkeypatch.setattr(review_stage, "approve_instruction", settled_then_approved)
    assert legacy.wake_graph_run(FA) and _run_status(env) == "running"
    node, row = legacy.instruction_node_of_ai_run(FA, INSTRUCTION_RUN)
    review_stage._after_instruction(FA, legacy.ai_run_record(INSTRUCTION_RUN), node, row)
    monkeypatch.setattr(review_stage, "approve_instruction", approve)
    assert _review_status(line_doc) == "approved"
    assert (_row(env, key)["status"], _row(env, key)["stage"]) == ("open", "instruction")
    assert _refusals(env) == [("result_stage", "run_idle")]
    assert env["jobs"] == [] and _run_status(env) == "idle"
    return key, line_doc


@pytest.mark.parametrize("read", ["run_read", "resume"])
def test_a4_the_run_read_right_after_a_refused_result_stage_starts_it_once(env, decided, monkeypatch, questions, read):
    """NR A-4: each silent return of ``_reconcile_instruction`` asserted on the 0084 state (``_handoff_pending``,
    ``node_ai_run_active``, ``stage_ai_run``, ``attempt_of_ai_run``, ``open_questions_of_ai_run``,
    ``written_instruction``), then the read (or a Resume) takes the approved instruction's result stage once; a second
    read takes nothing more. Before T#3 it returned at ``stage_ai_run`` None: the run's durable row has no
    ``action_scope``."""
    from flowgate.card_settings import legacy_adapter as legacy
    from flowgate.card_settings import review_stage

    key, line_doc = _refused_result_stage(env, monkeypatch, questions)
    run_id = _run_id(env)
    node, row = legacy.node_of_key(FA, key), legacy.node_run(run_id, key)
    assert review_stage._ai_card(FA, key) is not None
    assert review_stage._handoff_pending(FA, key) is False
    assert legacy.node_ai_run_active(FA, key) is False
    # the A-4 cause: its durable row tells no ``action_scope``, so ``stage_ai_run`` found no run before T#3
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
    assert job["stage"] == "result" and job["ref_doc_ids"] == [line_doc]
    assert len(_executions(env)) == execs + 1
    assert _row(env, key)["stage"] == "result" and _run_status(env) == "running"
    assert _refusals(env) == [("result_stage", "run_idle")]               # only seq 11's
    _current(env)
    assert len(env["jobs"]) == 1 and len(_executions(env)) == execs + 1


def test_a4_a_result_writer_s_durable_row_is_found_by_its_minted_token_s_action_scope(env, decided, monkeypatch):
    """The same cause on the result's reconciliation (``result_ai_run``): the writer's durable row has no
    ``action_scope``; the token its AI start minted (``new``) tells it."""
    from flowgate.card_settings import legacy_adapter as legacy

    key, result, _token = _in_review(env, monkeypatch)
    _ai_run_finished(env, WORK_RUN, _executions(env)[-1]["exec_id"])
    assert legacy.ai_run_record(WORK_RUN).get("action_scope") is None
    found = legacy.result_ai_run(FA, _run_id(env), key, result)
    assert found is not None and (found["run_id"], found["action_scope"]) == (WORK_RUN, "new")
    assert legacy.stage_ai_run(FA, _run_id(env), key)["run_id"] == WORK_RUN


# ── review rejection P1: a passed review whose own questions wait is not reconciled ─────────────

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
                                              ask=False, settings=(("instruction_review_count", 1),))
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
    assert job["stage"] == "result" and job["ref_doc_ids"] == [line_doc]
    assert approvals == [line_doc] and _review_status(line_doc) == "approved"
    _current(env)
    assert len(env["jobs"]) == 1 and len(_executions(env)) == execs + 1 and approvals == [line_doc]
    assert _refusals(env) == []
