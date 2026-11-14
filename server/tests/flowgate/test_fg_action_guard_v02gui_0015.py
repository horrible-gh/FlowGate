"""flowgate.v02gui.0015 T3 (NR flowgate.v02gui.0015.0006 §6-3, B flowgate.v02gui.0015.0001 §3-4) — an answer is never the
action it asked for: the common guard of the three re-invocations after a card AI's questions are answered
(``flowgate.card_settings.action_guard``).

On the real SQLite DB, routes and fixtures of ``test_fg_question_pending_0111`` (the Graph engine, its execution ledger, the
card stage ports, the run-end port, the question wait and the run read are the real ones; the asked questions and the
responder's start are its stand-ins). What the latest answers selected (``legacy.answered_requirements``) is a stand-in in
the Graph tests and read from the real ``question_items`` / ``answers`` rows in its own tests.

0005 incident form (Q2·Q3 "a person or the system makes a new attempt on the right base, then call again", nothing
made, the same attempt called again): with an option requiring ``new_attempt`` selected and the node's active attempt still
the asker's, the instruction / work / no_result step takes nothing — no execution request, no AI start, no approval, no
new attempt — the card is ``needs_intervention`` (``action_required:new_attempt``) with one ``action_required`` run event
however often the answers are told, the run read's reconciliation takes nothing, and the person's explicit command opens
the open card again as a new attempt (never the same attempt dispatched again).
"""
from __future__ import annotations

import json

import pytest

from test_fg_card_approval_0056 import _current, _node_run, _resume, _run_id
from test_fg_card_settings import (  # noqa: F401 — fixtures used by name
    legacy_cards, AI, FA, _sql, db_path, decided, env, setting,
)
from test_fg_card_settings_review import INSTRUCTION_RUN, WORK_RUN, _in_review, _review_status, _run_ended
from test_fg_question_pending_0111 import (  # noqa: F401 — fixtures used by name
    QUESTION, ROOT, _ai_run_finished, _answer, _asked_instruction, _asked_result, _attempts, _executions, _row,
    card_outputs_port, questions,
)

pytestmark = pytest.mark.usefixtures("legacy_cards")

REASON = "action_required:new_attempt"


@pytest.fixture()
def requires(monkeypatch):
    """``{asker AI run id: {requires, ...}}`` — what the latest answers to that run's questions selected."""
    from flowgate.card_settings import legacy_adapter as legacy

    state: dict[str, set] = {}
    monkeypatch.setattr(legacy, "answered_requirements", lambda asker: frozenset(state.get(asker, ())), raising=False)
    return state


# ── helpers ──────────────────────────────────────────────────────────────────

def _events(env):
    rows = _sql(env["db"], "SELECT reason, payload_json FROM graph_run_events WHERE cause = 'action_required' "
                           "ORDER BY event_seq")
    return [(r["reason"], json.loads(r["payload_json"])) for r in rows]


def _active(env, key):
    return [a["attempt_id"] for a in _attempts(env, key) if a["status"] == "active"]


def _held(env, key, *, asker, phase, execs, attempts):
    """Nothing called, approved or opened; the card ``needs_intervention`` with the guard's reason on the wire and the
    run's idle reason; exactly one ``action_required`` record of the asker."""
    assert env["jobs"] == []
    assert len(_executions(env)) == execs
    assert _attempts(env, key) == attempts
    run = _current(env)                         # the run read: its reconciliation + settle take nothing either
    wire = next(n for n in run["node_runs"] if n["node_key"] == key)
    assert wire["intervention"] == {"kind": "needs_intervention", "reason": REASON}
    assert (run["status"], run["idle_reason"]) == ("idle", "needs_intervention")
    assert env["jobs"] == [] and len(_executions(env)) == execs
    [(reason, payload)] = _events(env)
    assert reason == REASON
    assert (payload["asker_ai_run_id"], payload["phase"], payload["node_key"]) == (asker, phase, key)
    assert payload["attempt_id"] == _active(env, key)[0]
    return run


def _written_result_asked(env, monkeypatch, questions, settings=()):
    """A result writer (``WORK_RUN``) that registered its result and asked question 905 before its clean end: the
    result's step (its review start or automatic approval) waits for the answer. -> (node key, result)."""
    from flowgate.card_settings import legacy_adapter as legacy

    key, result, _token = _in_review(env, monkeypatch, settings)
    env["jobs"].clear()
    latest = _executions(env)[-1]["exec_id"]
    _ai_run_finished(env, WORK_RUN, latest)
    questions["asked"][WORK_RUN] = [dict(QUESTION, id=905)]
    _run_ended(WORK_RUN, graph_exec_id=latest)
    legacy.settle_graph_run(FA)
    assert questions["responders"] == [(905, "aip_review")]
    assert (_row(env, key)["status"], _review_status(result)) == ("review", "pending_review")
    return key, result


# ── instruction phase ────────────────────────────────────────────────────────

@pytest.mark.parametrize("how", ["human", "ai", "both", "chat"])
def test_an_instruction_stage_answered_with_a_new_attempt_required_is_not_written_again(env, decided, monkeypatch,
                                                                                       questions, requires, how):
    key, _line_doc, _exec = _asked_instruction(env, monkeypatch, questions)
    attempts, execs = _attempts(env, key), len(_executions(env))
    requires[INSTRUCTION_RUN] = {"new_attempt"}

    _answer(questions, INSTRUCTION_RUN, how)

    _held(env, key, asker=INSTRUCTION_RUN, phase="instruction", execs=execs, attempts=attempts)
    _answer(questions, INSTRUCTION_RUN, "both")                         # told again: still nothing, one record
    _held(env, key, asker=INSTRUCTION_RUN, phase="instruction", execs=execs, attempts=attempts)


def test_a_written_instruction_answered_with_a_new_attempt_required_is_neither_approved_nor_followed(
        env, decided, monkeypatch, questions, requires):
    key, line_doc, _exec = _asked_instruction(env, monkeypatch, questions, stop_code=None,
                                              body="## 작업\n\n지시서\n")
    attempts, execs = _attempts(env, key), len(_executions(env))
    requires[INSTRUCTION_RUN] = {"new_attempt"}

    _answer(questions, INSTRUCTION_RUN, "human")

    _held(env, key, asker=INSTRUCTION_RUN, phase="instruction", execs=execs, attempts=attempts)
    assert _review_status(line_doc) != "approved"
    assert _row(env, key)["stage"] == "instruction"


def test_the_chat_outlook_tells_the_held_card_and_its_next_action(env, decided, monkeypatch, questions, requires):
    from flowgate.card_settings import question_wait

    _key, _doc, _exec = _asked_instruction(env, monkeypatch, questions)
    requires[INSTRUCTION_RUN] = {"new_attempt"}
    questions["asked"][INSTRUCTION_RUN] = []
    assert question_wait.chat_answer_outlook(FA, INSTRUCTION_RUN) == {
        "state": "action_required", "reason": REASON, "next_action": "explicit_command_opens_new_attempt"}
    requires[INSTRUCTION_RUN] = set()
    assert question_wait.chat_answer_outlook(FA, INSTRUCTION_RUN)["state"] == "resumes_after_chat_run"


def test_the_explicit_command_opens_the_held_card_as_a_new_attempt(env, decided, monkeypatch, questions, requires):
    """The next action: the person's command replaces the held attempt (``reopen_new_attempt``) — the old one
    invalidated, a new one active and dispatched — instead of dispatching the same attempt again; the new attempt is no
    longer held."""
    key, _doc, _exec = _asked_instruction(env, monkeypatch, questions)
    requires[INSTRUCTION_RUN] = {"new_attempt"}
    _answer(questions, INSTRUCTION_RUN, "human")
    [old] = _active(env, key)
    assert env["jobs"] == []

    _resume(env)

    statuses = {a["attempt_id"]: a["status"] for a in _attempts(env, key)}
    assert statuses[old] == "invalidated"
    [new] = _active(env, key)
    assert new != old
    assert len(env["jobs"]) == 1 and env["jobs"][0]["stage"] == "instruction"
    assert _node_run(env, key)["intervention"] is None


# ── no_result phase ──────────────────────────────────────────────────────────

def test_a_result_writer_answered_with_a_new_attempt_required_is_not_run_again(env, decided, monkeypatch, questions,
                                                                              requires):
    key, _exec = _asked_result(env, monkeypatch, questions)
    attempts, execs = _attempts(env, key), len(_executions(env))
    requires[WORK_RUN] = {"new_attempt"}

    _answer(questions, WORK_RUN, "both")

    _held(env, key, asker=WORK_RUN, phase="no_result", execs=execs, attempts=attempts)
    [old] = _active(env, key)
    _resume(env)                                                        # before T3: the same attempt dispatched again
    assert {a["attempt_id"]: a["status"] for a in _attempts(env, key)}[old] == "invalidated"
    assert len(_active(env, key)) == 1 and _active(env, key) != [old]


def test_answers_that_require_nothing_still_retry_the_writer_once(env, decided, monkeypatch, questions, requires):
    """The guard bites only with a requirement selected: the 0111 retry goes on as before."""
    key, _exec = _asked_result(env, monkeypatch, questions)
    attempts, execs = _attempts(env, key), len(_executions(env))

    _answer(questions, WORK_RUN, "human")

    [job] = env["jobs"]
    assert job["stage"] == "result" and len(_executions(env)) == execs + 1 and _attempts(env, key) == attempts
    assert _events(env) == []


def test_an_unreadable_answer_holds_nothing(env, decided, monkeypatch, questions):
    from flowgate.card_settings import legacy_adapter as legacy

    def unreadable(asker):
        raise RuntimeError("answers unreadable")

    monkeypatch.setattr(legacy, "answered_requirements", unreadable, raising=False)
    key, _exec = _asked_result(env, monkeypatch, questions)
    execs = len(_executions(env))
    _answer(questions, WORK_RUN, "human")
    assert len(env["jobs"]) == 1 and len(_executions(env)) == execs + 1 and _events(env) == []


# ── work phase ───────────────────────────────────────────────────────────────

def test_a_written_result_answered_with_a_new_attempt_required_is_not_approved(env, decided, monkeypatch, questions,
                                                                              requires):
    """``_after_work`` with no review: the automatic approval is not made, the run read's reconciliation approves
    nothing either; the card in review stays (never reopened)."""
    key, result = _written_result_asked(env, monkeypatch, questions)
    attempts, execs = _attempts(env, key), len(_executions(env))
    requires[WORK_RUN] = {"new_attempt"}

    _answer(questions, WORK_RUN, "both")

    _held(env, key, asker=WORK_RUN, phase="work", execs=execs, attempts=attempts)
    _current(env)
    assert (_row(env, key)["status"], _review_status(result)) == ("review", "pending_review")


def test_a_written_result_answered_with_a_new_attempt_required_starts_no_review(env, decided, monkeypatch, questions,
                                                                               requires):
    from flowgate.card_settings import legacy_adapter as legacy

    starts: list = []
    monkeypatch.setattr(legacy, "start_review_loop", lambda *a, **kw: starts.append(a) or {"run_id": "air_review"})
    key, result = _written_result_asked(env, monkeypatch, questions, (("review_count", 2), ("review_ai", "aip_review")))
    attempts, execs = _attempts(env, key), len(_executions(env))
    requires[WORK_RUN] = {"new_attempt"}

    _answer(questions, WORK_RUN, "human")

    _held(env, key, asker=WORK_RUN, phase="work", execs=execs, attempts=attempts)
    assert starts == []


# ── the requirement as stored: options and answers ───────────────────────────

def _ask_stored(asker, options):
    from modules.flow_gate.services import q_service

    added = q_service.add_questions(ROOT, [{"body": "기준이 뒤처졌습니다. 어떻게 할까요?", "options": options}],
                                    asker_kind="ai", created_by="u_kim", project_id="flowgate",
                                    asker_provenance={"ai_run_id": asker})
    return added["added_item_ids"][0]


def test_an_option_requiring_a_new_attempt_is_stored_and_read_from_the_latest_answer(env):
    from flowgate.card_settings import legacy_adapter as legacy
    from modules.flow_gate.services import q_service

    item_id = _ask_stored("air_asker_stored", [{"label": "새 기준으로 새 attempt를 만든 뒤 재호출", "requires": "new_attempt"},
                                              "지금 기준으로 계속"])
    [item] = [i for i in q_service.get_qa_detail(ROOT)["items"] if i["id"] == item_id]
    assert item["options"] == [{"id": "o1", "label": "새 기준으로 새 attempt를 만든 뒤 재호출", "requires": "new_attempt"},
                               {"id": "o2", "label": "지금 기준으로 계속"}]
    assert legacy.answered_requirements("air_asker_stored") == frozenset()          # not answered yet
    q_service.register_answer(ROOT, item_id, "", author_kind="human", author_id="u_kim", selected_option_ids=["o1"])
    assert legacy.answered_requirements("air_asker_stored") == frozenset({"new_attempt"})
    q_service.register_answer(ROOT, item_id, "", author_kind="human", author_id="u_kim", selected_option_ids=["o2"])
    assert legacy.answered_requirements("air_asker_stored") == frozenset()          # the latest answer decides
    assert legacy.answered_requirements("air_someone_else") == frozenset()


def test_plain_options_stay_byte_identical_and_bad_requirements_are_refused(env):
    from fastapi import HTTPException

    from modules.flow_gate.services import q_service

    item_id = _ask_stored("air_plain", ["A안", {"label": "B안"}])
    [item] = [i for i in q_service.get_qa_detail(ROOT)["items"] if i["id"] == item_id]
    assert item["options"] == [{"id": "o1", "label": "A안"}, {"id": "o2", "label": "B안"}]
    for bad, fragment in (([{"label": "x", "requires": "reset_hard"}], "requires must be one of"),
                          ([{"id": "o1", "label": "x"}], "string label"),
                          ([{"label": "x", "requires": "new_attempt", "extra": 1}], "string label")):
        with pytest.raises(HTTPException) as exc:
            _ask_stored("air_bad", bad)
        assert exc.value.status_code == 400 and fragment in exc.value.detail
