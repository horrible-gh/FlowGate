"""0071 T6 replaces the old 'answer is never the action' contract.
Answered new_attempt requests continue automatically: bounded fenced replacement
when possible, otherwise ordinary review/approval. Human pause/stop remains final.
Real SQLite Graph/stage fixtures; only questions and provider starts are fakes.
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


def test_answer_replaces_attempt_under_callers_shared_gate(env, decided, monkeypatch, questions, requires):
    from flowgate.graph_run import fencing

    key, _doc, _exec = _asked_instruction(env, monkeypatch, questions, settings=(("restart", 1),))
    [old] = _active(env, key)
    requires[INSTRUCTION_RUN] = {"new_attempt"}
    with fencing.shared(FA):
        _answer(questions, INSTRUCTION_RUN, "human")
    [new] = _active(env, key)
    assert new != old
    assert len(env["jobs"]) == 1
    assert len(_attempts(env, key)) == 2
    assert _row(env, key)["status"] == "open"


@pytest.mark.parametrize("how", ["human", "ai", "both", "chat"])
def test_answer_automatically_replaces_attempt_once(env, decided, monkeypatch, questions, requires, how):
    key, _doc, _exec = _asked_instruction(env, monkeypatch, questions, settings=(("restart", 1),))
    [old] = _active(env, key)
    requires[INSTRUCTION_RUN] = {"new_attempt"}
    _answer(questions, INSTRUCTION_RUN, how)
    [new] = _active(env, key)
    assert new != old
    assert len(env["jobs"]) == 1
    assert {a["attempt_id"]: a["status"] for a in _attempts(env, key)}[old] == "invalidated"
    _answer(questions, INSTRUCTION_RUN, "both")
    assert _active(env, key) == [new] and len(env["jobs"]) == 1
    assert _node_run(env, key)["intervention"] is None


def test_restart_budget_is_consumed_across_asking_runs(env, decided, monkeypatch, questions, requires):
    from test_fg_card_settings_review import _start_ai
    from test_fg_question_pending_0111 import _ended
    key, doc, _exec = _asked_instruction(env, monkeypatch, questions, settings=(("restart", 1),))
    requires[INSTRUCTION_RUN] = {"new_attempt"}
    _answer(questions, INSTRUCTION_RUN, "human")
    [new] = _active(env, key)
    [job] = env["jobs"]
    env["jobs"].clear()
    asker = "air_second_asking"
    _start_ai(monkeypatch, job, asker)
    questions["asked"][asker] = [dict(QUESTION)]
    _ai_run_finished(env, asker, job["graph_exec_id"], stop_code="question_pending", doc_ref=doc)
    _ended(env, asker, job["graph_exec_id"], stop_code="question_pending", doc_ref=doc)
    requires[asker] = {"new_attempt"}
    _answer(questions, asker, "both")
    assert _active(env, key) == [new] and len(_attempts(env, key)) == 2
    assert len(env["jobs"]) == 1  # same-attempt continuation; no third attempt


def test_result_writer_automatically_retries_in_new_attempt(env, decided, monkeypatch, questions, requires):
    key, _exec = _asked_result(env, monkeypatch, questions)
    setting(env, FA, key, "restart", 1)
    [old] = _active(env, key)
    requires[WORK_RUN] = {"new_attempt"}
    _answer(questions, WORK_RUN, "both")
    assert _active(env, key) != [old] and len(env["jobs"]) == 1


def test_spent_budget_continues_on_same_attempt(env, decided, monkeypatch, questions, requires):
    key, _doc, _exec = _asked_instruction(env, monkeypatch, questions, settings=(("restart", 0),))
    [old] = _active(env, key)
    requires[INSTRUCTION_RUN] = {"new_attempt"}
    _answer(questions, INSTRUCTION_RUN, "both")
    assert _active(env, key) == [old] and len(env["jobs"]) == 1
    assert _node_run(env, key)["intervention"] is None


@pytest.mark.parametrize("review_count,budget", [(0, 0), (2, 0), (0, 1)])
def test_answer_with_result_continues_to_approval_or_review(env, decided, monkeypatch, questions, requires, review_count, budget):
    from flowgate.card_settings import legacy_adapter as legacy
    starts = []
    monkeypatch.setattr(legacy, "start_review_loop", lambda *a, **kw: starts.append(a) or {"run_id": "air_review"})
    key, result = _written_result_asked(env, monkeypatch, questions,
        (("restart", budget), ("review_count", review_count), ("review_ai", "aip_review")))
    attempts = _attempts(env, key)
    requires[WORK_RUN] = {"new_attempt"}
    _answer(questions, WORK_RUN, "both")
    if review_count:
        # The fake provider leaves its execution unacquired, so replay may ask it
        # again. Durable intent, rather than fake start calls, must be unique.
        assert starts and _review_status(result) == "pending_review"
        assert len(_sql(env["db"], "SELECT exec_id FROM graph_attempt_runs WHERE kind = 'review'")) == 1
    else:
        assert _review_status(result) == "approved"
        assert _row(env, key)["status"] == "done"
    assert len(_attempts(env, key)) == len(attempts)


@pytest.mark.parametrize("status,reason", [("paused", None), ("idle", "stopped"), ("stopping", None)])
def test_answer_never_wakes_person_stopped_run(env, decided, monkeypatch, questions, requires, status, reason):
    key, _doc, _exec = _asked_instruction(env, monkeypatch, questions, settings=(("restart", 1),))
    before = _attempts(env, key)
    _sql(env["db"], "UPDATE graph_runs SET status = ?, idle_reason = ? WHERE run_id = ?",
         (status, reason, _run_id(env)))
    requires[INSTRUCTION_RUN] = {"new_attempt"}
    _answer(questions, INSTRUCTION_RUN, "both")
    assert _attempts(env, key) == before and env["jobs"] == []
    assert _sql(env["db"], "SELECT status FROM graph_runs WHERE run_id = ?", (_run_id(env),))[0]["status"] == status


def test_chat_outlook_predicts_automatic_continuation(env, decided, monkeypatch, questions, requires):
    from flowgate.card_settings import question_wait
    _asked_instruction(env, monkeypatch, questions)
    requires[INSTRUCTION_RUN] = {"new_attempt"}
    questions["asked"][INSTRUCTION_RUN] = []
    assert question_wait.chat_answer_outlook(FA, INSTRUCTION_RUN)["state"] == "resumes_after_chat_run"


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
