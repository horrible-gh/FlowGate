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
    legacy_cards, pre_feature_attempts,
    AI, FA, _run_one_card, _sql, _start, db_path, decided, env, setting,
)
from test_fg_card_settings_review import (
    INSTRUCTION_RUN, WORK_RUN, _admin_and_reviewer, _review_status, _start_ai, _through_instruction, _write_written,
)
# flowgate.v02.0113 T#3 (T §6.4): this suite's cards are cards made before the AI role feature (their legacy fields:
# assignee, run_ai, review_count / review_ai — L-1 §2.4.5); the stage assignment feature stays on, so each new attempt
# runs the assigned path (L-2) resolved from those fields. Pre-feature attempts are made explicitly
# (``pre_feature_attempts``) by the tests of the pre-feature contract.
pytestmark = pytest.mark.usefixtures("legacy_cards")

ROOT = f"{FA}.0001-R"
RESPONDER_RUN = "air_card_responder"
CHAT_RUN = "aiv2_chat_answering"
QUESTION = {"id": 901, "anchor_doc_id": ROOT, "asker_kind": "ai", "responder_state": None,
            "asker_actual_provider_id": "aip_assignee"}


@pytest.fixture(autouse=True)
def card_outputs_port():
    """flowgate.v02.0119 T#4 (T-3): the card's required outputs port (``card_completion_ports.pending_outputs``),
    registered as ``startup`` registers it — it replaced the removed card approval port."""
    from flowgate.card_completion import outputs
    from modules.flow_gate.graph_workflow.services import card_completion_ports as ports

    ports.register(ports.PENDING_OUTPUTS, outputs.pending_outputs)
    yield
    ports.unregister(ports.PENDING_OUTPUTS)


@pytest.fixture()
def questions(monkeypatch):
    """The questions each AI run asked, still unanswered (``{ai run id: [item]}``); the responder starts recorded."""
    from flowgate.card_settings import legacy_adapter as legacy
    from modules.flow_gate.services.ai_invoke import finalize

    asked: dict[str, list[dict]] = {}
    state = {"asked": asked, "responders": [], "answered_by": {}, "answered_as_author": {}}
    monkeypatch.setattr(finalize, "open_questions_raised_by_run",
                        lambda group_id, run_id: [dict(i) for i in asked.get(run_id, [])])
    monkeypatch.setattr(legacy, "independent_question_responder",
                        lambda project_id, asker, candidates: ("aip_review", "card_review_ai"))
    monkeypatch.setattr(legacy, "ask_responder",
                        lambda doc_id, item_id, issued_to, provider, source, **kw:
                        state["responders"].append((item_id, provider)) or {"run_id": RESPONDER_RUN})
    monkeypatch.setattr(legacy, "askers_answered_by", lambda run_id: set(state["answered_by"].get(run_id, ())))
    # flowgate.v02gui.0010: the questions a chat run answered itself (its answer rows' author_ai_run_id)
    monkeypatch.setattr(legacy, "askers_answered_as_author",
                        lambda run_id: set(state["answered_as_author"].get(run_id, ())))
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
    if how == "chat":
        # flowgate.v02gui.0010: a chat AI's answer (author_kind ai) is skipped by the person hook and followed at the
        # chat run's end instead
        question_wait.on_question_answered({"author_kind": "ai", "group_id": FA, "asker_ai_run_id": asker})
        questions["answered_as_author"][CHAT_RUN] = {asker}
        question_wait.follow({"run_id": CHAT_RUN, "group_id": FA, "action_scope": "chat"})


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

@pytest.mark.parametrize("how", ["human", "ai", "both", "chat"])
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

@pytest.mark.parametrize("how", ["human", "ai", "chat"])
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
    assert job["stage"] == "result" and job["ref_doc_ids"] == [ROOT, line_doc]   # no instruction stage again
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







# ── 6: result stage, nothing written (question_pending) ─────────────────────





# ── 8: a Resume while waiting ───────────────────────────────────────────────



# ── 9: the existing paths ────────────────────────────────────────────────────







# 0113 T#3 (T §6.4) pre-feature contract: the retry is told by a new node token; an assigned attempt's RETRY_SAME_ATTEMPT starts the same AI with no new node token (L-2 §2.6/§2.10) — test_fg_ai_roles_l2_reconcile.py::test_an_explicit_command_retries_a_no_result_writer_*
