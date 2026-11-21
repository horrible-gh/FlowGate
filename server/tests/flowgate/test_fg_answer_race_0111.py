"""(T-A2, NR the earlier change A-2 / A-4) — a card step that goes on once its questions are
answered is taken under ``legacy.person_hold`` with ``idle_ok`` in every phase (``question_wait._answered``: instruction,
review, work, no_result), so a run judged idle between the wake and the step never refuses it; and the run read's
reconciliation of an approved instruction whose result stage was refused (the 0084 state after seq 11).

On the same real SQLite DB, routes and fixtures as ``test_fg_question_pending_0111``. The race is injected in order
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
    legacy_cards, AI, FA, _run_one_card, _sql, _start, db_path, decided, env, setting,
)
from test_fg_card_settings_review import (
    INSTRUCTION_RUN, REVIEW_RUN, WORK_RUN, _in_review, _review_status, _run_ended,
)
from test_fg_question_pending_0111 import (  # noqa: F401 — fixtures used by name
    QUESTION, _after_answers, _ai_run_finished, _answer, _asked_instruction, _asked_result, _attempts, _executions,
    _row, card_outputs_port, questions,
)


# Imported helpers do not inherit their module usefixtures marker.
pytestmark = pytest.mark.usefixtures("legacy_cards", "question_index")


@pytest.fixture
def question_index(questions, monkeypatch):
    from flowgate.card_settings import legacy_adapter as legacy

    ask = legacy.ask_responder

    def claim(doc_id, item_id, issued_to, provider, source, **kwargs):
        result = ask(doc_id, item_id, issued_to, provider, source, **kwargs)
        for items in questions["asked"].values():
            for item in items:
                if item["id"] == item_id:
                    item.update(responder_state="running", responder_run_id=result["run_id"])
        return result

    # The real answer service claims the item before recovery reads it again.
    monkeypatch.setattr(legacy, "ask_responder", claim)
    monkeypatch.setattr(legacy, "open_ai_questions", lambda flow_id: [
        {**item, "asker_ai_run_id": run_id}
        for run_id, items in questions["asked"].items() for item in items
    ])


@pytest.fixture()
def racing_wake(env, monkeypatch):
    """seq 8 -> 9: the wake, then at once a settle that judges the run idle (nothing works yet). -> the
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

    pending = _sql(env["db"], "SELECT exec_id FROM graph_attempt_runs WHERE kind = 'review' "
                   "AND status = 'requested' ORDER BY created_at, exec_id")
    if pending:
        _review_ended(env, questions, doc_id, pending[-1]["exec_id"], "review_passed", item=904)
        assert questions["responders"][-1] == (904, "aip_review")
        return

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
                                "mode": "single", "issued_to": "u_kim", "provider_id": "aip_review",
                                "end_reason": "exited", "stop_code": None, "outcome": "complete",
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
              attempt_id=None, failure=None):
        if graph_exec_id:
            if graph_exec_id not in starts:
                starts.append(graph_exec_id)
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
                                "provider_id": "aip_review", "end_reason": "exited", "stop_code": None,
                                "outcome": "complete", "document_review_loop": {"current_stage": "stopped", "stop_reason": stop_reason}})
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


# ── instruction phase ('s continuation, kept) ─────────────────────────────

def test_an_answered_written_instruction_raced_idle_starts_its_result_stage_once(env, decided, monkeypatch, questions,
                                                                                racing_wake):
    key, line_doc, _exec = _asked_instruction(env, monkeypatch, questions, stop_code=None, body="## 작업\n\n지시서\n")
    attempts, execs = _attempts(env, key), len(_executions(env))

    _answer(questions, INSTRUCTION_RUN, "both")

    _once_and_unrefused(env, racing_wake)
    job = _after_answers(env, key, execs, attempts)
    assert job["stage"] == "result" and job["ref_doc_ids"] == [f"{FA}.0001-R", line_doc]
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

    reviews = _ledgered_review_start(monkeypatch)
    key, line_doc, _exec = _asked_instruction(env, monkeypatch, questions, stop_code=None, body="## 작업\n\n지시서\n",
                                              ask=False, settings=(("instruction_review_count", 1), ("instruction_review_ai", "aip_review")))
    assert len(reviews) == 1 and env["jobs"] == []                     # its review first
    _passed_review(env, questions, line_doc)
    assert _review_status(line_doc) != "approved" and _run_status(env) == "idle"
    attempts, execs = _attempts(env, key), len(_executions(env))

    _answer(questions, REVIEW_RUN, "both")

    _once_and_unrefused(env, racing_wake)
    job = _after_answers(env, key, execs, attempts)
    assert job["stage"] == "result" and job["ref_doc_ids"] == [f"{FA}.0001-R", line_doc]
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








# ── no_result phase (``retry_result_stage``) ─────────────────────────────────





# ── A-4: the run read right after the result stage was refused ───────────────

def _refused_result_stage(env, monkeypatch, questions):
    """The 0084 state after seq 11, made by the continuation as it was before the earlier implementation (``_answered`` -> ``_after_instruction``):
    the wake (seq 8), its approval's hold judged on the running run, the settle that judges it idle inside that hold (seq
    9), the instruction's approval (seq 10) and the result stage refused on the idle run (seq 11, ``result_stage`` /
    ``run_idle``). -> (node key, the instruction)."""
    from flowgate.card_settings import legacy_adapter as legacy
    from flowgate.card_settings import review_stage

    key, line_doc, _exec = _asked_instruction(env, monkeypatch, questions, stop_code=None, body="## 작업\n\n지시서\n")
    approve = review_stage.approve_instruction

    def settled_then_approved(*args, **kwargs):
        legacy.settle_graph_run(FA)
        assert _run_status(env) == "idle"
        return approve(*args, **kwargs)

    questions["asked"][INSTRUCTION_RUN] = []
    monkeypatch.setattr(review_stage, "approve_instruction", settled_then_approved)
    assert legacy.wake_graph_run(FA) and _run_status(env) == "running"
    node, row = legacy.instruction_node_of_ai_run(FA, INSTRUCTION_RUN)
    review_stage._after_instruction(FA, legacy.ai_run_record(INSTRUCTION_RUN), node, row)
    monkeypatch.setattr(review_stage, "approve_instruction", approve)
    assert _review_status(line_doc) == "approved"
    assert (_row(env, key)["status"], _row(env, key)["stage"]) == ("open", "instruction")
    assert _refusals(env) == [("instruction_result_stage", "run_idle")]
    assert env["jobs"] == [] and _run_status(env) == "idle"
    return key, line_doc






# ── review rejection P1: a passed review whose own questions wait is not reconciled ─────────────
