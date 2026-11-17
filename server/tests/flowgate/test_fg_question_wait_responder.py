"""T0013: Graph question responders use an independent reviewer/default or await a person."""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
os.environ.setdefault("ALLOWED_ORIGIN", "http://localhost")
os.environ.setdefault("CONTEXT", "/flowgate")
os.environ.setdefault("DB_TYPE", "sqlite")

from flowgate.card_settings import question_wait as wait
from modules.flow_gate.services import q_service
from modules.flow_gate.services.ai_invoke import review


@pytest.fixture
def routing(monkeypatch):
    from flowgate.ai_roles import role_store, record_store
    monkeypatch.setattr(record_store, "available", lambda: False)
    monkeypatch.setattr(role_store, "available", lambda: False)  # explicit legacy/no-role-store fixture
    stored = {"instruction": True, "run_ai": "worker", "instruction_ai": "worker", "review_ai": "reviewer"}
    chain = [{"id": "worker"}, {"id": "reviewer"}, {"id": "third"}]
    item = {"id": 518, "anchor_doc_id": "flowgate.v02.0110.0001-B", "answer_count": 0}
    calls, failed = [], []
    monkeypatch.setattr(wait.repository, "values", lambda *a: stored)
    monkeypatch.setattr(wait.legacy, "project_of", lambda fid: "flowgate")
    monkeypatch.setattr(wait.legacy, "default_reviewer", lambda pid: "worker")
    monkeypatch.setattr(wait.legacy, "flow_ai_busy", lambda fid: False)
    monkeypatch.setattr(review.ai_settings_service, "resolve_effective", lambda pid: {"providers": chain})
    monkeypatch.setattr(wait.legacy, "ask_responder", lambda *a, **kw: calls.append(a) or {"run_id": "answer-run"})
    monkeypatch.setattr(wait, "open_questions", lambda *a: [item])

    def fail(item_id, code, message, **kw):
        failed.append((item_id, code))
        item.update(responder_state="failed", responder_error_code=code)
        return True

    monkeypatch.setattr(q_service, "mark_responder_failed", fail)
    return stored, chain, item, calls, failed


NODE = {"node_key": "card", "node_type": "document", "doc_type": "T", "assignee_provider_id": "worker"}
RUN = {"run_id": "asker-run", "provider_id": "worker", "issued_to": "owner"}


@pytest.mark.parametrize("instruction", [False, True])
def test_instruction_card_uses_review_ai_for_every_asking_stage(routing, instruction):
    stored, chain, item, calls, failed = routing
    assert wait.hold("flow", RUN, NODE, asked_by_instruction=instruction) is True
    assert calls[0][3:] == ("reviewer", "card_review_ai")
    assert failed == []


@pytest.mark.parametrize("reviewer", [None, "worker", "disabled"])
def test_same_default_as_asker_is_skipped_for_next_enabled_provider(routing, reviewer):
    stored, chain, item, calls, failed = routing
    stored["review_ai"] = reviewer
    assert wait._dispatch("flow", NODE, RUN, [item], False) is True
    assert calls[0][3:] == ("reviewer", "default")
    assert failed == []


def test_single_provider_marks_failure_and_keeps_question_open_for_human(routing):
    stored, chain, item, calls, failed = routing
    chain[:] = [{"id": "worker"}]
    assert wait.hold("flow", RUN, NODE) is True
    assert calls == []
    assert failed == [(518, "no_independent_responder")]
    assert item["answer_count"] == 0
    assert wait._dispatch("flow", NODE, RUN, [item], False) is False
    assert len(failed) == 1


def test_unknown_asker_provider_never_dispatches_an_unchecked_default(routing):
    stored, chain, item, calls, failed = routing
    assert wait._dispatch("flow", {**NODE, "assignee_provider_id": "third"},
                          {"run_id": "legacy-asker"}, [item], False) is False
    assert calls == []
    assert failed == [(518, "no_independent_responder")]


def test_question_actual_provider_wins_over_header_requested_and_run_fields(routing):
    stored, chain, item, calls, failed = routing
    item["asker_actual_provider_id"] = "reviewer"
    run = {**RUN, "requested_provider_id": "third", "continuation_base_provider_id": "third"}
    assert wait._dispatch("flow", NODE, run, [item], False) is True
    assert calls[0][3] == "worker"
    assert calls[0][3] != item["asker_actual_provider_id"]


def test_enabled_chain_read_failure_marks_human_wait(routing, monkeypatch):
    stored, chain, item, calls, failed = routing
    def unavailable(pid):
        raise RuntimeError("settings unavailable")
    monkeypatch.setattr(review.ai_settings_service, "resolve_effective", unavailable)
    assert wait._dispatch("flow", NODE, RUN, [item], False) is False
    assert calls == []
    assert failed == [(518, "no_independent_responder")]


@pytest.mark.parametrize("locale,words", [
    ("ko", ("질의자 provider", "독립 기본 AI", "사람 답변")),
    ("en", ("actual asker", "independent default AI", "human answer")),
    ("ja", ("質問者の実際のprovider", "独立した既定のAI", "人の回答")),
])
def test_question_help_explains_independence_and_human_wait(locale, words):
    from modules.flow_gate.services.help_catalog import QUESTION_HELP_COPY
    note = QUESTION_HELP_COPY[locale]["continue_note"]
    assert all(word in note for word in words)


@pytest.mark.parametrize("state,code,run_id,attempts,retry", [
    (None, None, None, 0, True),
    ("failed", "run_in_progress", None, 0, True),
    ("failed", "run_in_progress", "claimed", 1, False),
    ("failed", "run_in_progress", None, 1, False),
    ("failed", "provider_failed", None, 0, False),
    ("failed", "no_independent_responder", None, 0, False),
    ("user_decision", "run_in_progress", None, 0, False),
    ("dispatched", None, "working", 1, False),
])
def test_admission_contention_retries_only_an_unstarted_response(
        routing, state, code, run_id, attempts, retry):
    _, _, item, calls, failed = routing
    item.update(responder_state=state, responder_error_code=code,
                responder_run_id=run_id, responder_attempts=attempts)
    assert wait._dispatch("flow", NODE, RUN, [item], False) is retry
    assert len(calls) == int(retry)


@pytest.mark.parametrize("trigger", ["ended", "reconcile"])
def test_busy_admission_is_retried_after_other_run_ends(routing, monkeypatch, trigger):
    """Real answer service records the race; Graph follow must not strand its item."""
    from fastapi import HTTPException
    from modules.flow_gate.services import q_answer_invoke_service as answer
    _, _, item, calls, failed = routing
    item["asker_ai_run_id"] = RUN["run_id"]
    busy = [True]
    admitted = []
    monkeypatch.setattr(answer, "_live_responder_run_id", lambda _: None)
    monkeypatch.setattr(answer, "_ai_answer_count", lambda _: 0)

    def start(**kw):
        if busy[0]:
            raise HTTPException(409, detail={"code": "run_in_progress"})
        admitted.append(kw)
        item.update(responder_state="dispatched", responder_run_id="answer-run", responder_attempts=1)
        return {"run_id": "answer-run"}

    monkeypatch.setattr(answer.ai_invoke_service, "start_run", start)
    monkeypatch.setattr(wait.legacy, "ask_responder", lambda *a, **kw: answer.dispatch_answer_run(
        doc={"doc_id": item["anchor_doc_id"], "group_id": "flow", "project_id": "flowgate"},
        item=item, issued_to="owner", api_base_url="http://localhost", provider_id="reviewer"))
    monkeypatch.setattr(wait.legacy, "active_run_id", lambda _: "graph-run")
    monkeypatch.setattr(wait.legacy, "open_ai_questions", lambda _: [item])
    monkeypatch.setattr(wait.legacy, "askers_answered_by", lambda _: set())
    monkeypatch.setattr(wait.legacy, "ai_run_record", lambda _: {**RUN, "group_id": "flow"})
    monkeypatch.setattr(wait, "_waiting", lambda *a: ("work", NODE, None))
    assert not wait._dispatch("flow", NODE, RUN, [item], False)
    assert failed == [(518, "run_in_progress")]
    assert admitted == [] and not item.get("responder_run_id")
    busy[0] = False
    monkeypatch.setattr(wait.legacy, "graph_run_state", lambda _: ("idle", "no_targets"))
    def recover():
        if trigger == "ended":
            wait.follow({"run_id": "other-run", "group_id": "flow"})
        else:
            wait.reconcile("flow")
    recover()
    assert len(admitted) == 1
    recover()
    assert len(admitted) == 1  # repeated recovery cannot start a second responder


@pytest.mark.parametrize("state", [None, ("paused", None), ("stopping", None), ("idle", "stopped")])
def test_recovery_never_starts_a_paused_or_stopped_command(monkeypatch, state):
    monkeypatch.setattr(wait.legacy, "graph_run_state", lambda _: state)
    calls = []
    monkeypatch.setattr(wait, "_dispatch_waiting", lambda _: calls.append(True))
    wait.reconcile("flow")
    assert calls == []


@pytest.mark.parametrize("state", ["user_decision", "dispatched", "failed"])
def test_recovery_preserves_claims_real_failures_and_human_decisions(routing, monkeypatch, state):
    _, _, item, calls, _ = routing
    item.update(asker_ai_run_id=RUN["run_id"], responder_state=state, responder_error_code="provider_failed")
    monkeypatch.setattr(wait.legacy, "graph_run_state", lambda _: ("idle", "no_targets"))
    monkeypatch.setattr(wait.legacy, "active_run_id", lambda _: "graph-run")
    monkeypatch.setattr(wait.legacy, "open_ai_questions", lambda _: [item])
    wait.reconcile("flow")
    assert calls == []


def test_stage_reconciliation_recovers_questions_without_a_run_end(monkeypatch):
    from flowgate.card_settings import review_stage
    monkeypatch.setattr(review_stage.repository, "available", lambda: True)
    monkeypatch.setattr(review_stage.legacy, "graph_owned", lambda _: True)
    monkeypatch.setattr(review_stage.legacy, "active_run", lambda _: {"run_id": "r"})
    monkeypatch.setattr(review_stage.legacy, "person_stage_open", lambda *a: True)
    monkeypatch.setattr(review_stage.legacy, "node_runs", lambda _: [])
    calls = []
    monkeypatch.setattr(wait, "reconcile", lambda flow: calls.append(flow))
    review_stage.reconcile("flow")
    assert calls == ["flow"]


def test_question_badge_details_include_handoff_and_cross_document_asker(monkeypatch):
    from modules.flow_gate.graph_workflow.services import run_service
    from modules.flow_gate.graph_workflow.store import assignment_store
    from modules.flow_gate.db import question_items
    from modules.flow_gate.services.ai_invoke import finalize
    item = {"id": 1, "seq": 629, "answer_count": 0, "responder_state": "user_decision",
            "responder_error_code": "user_decision_required", "responder_error_message": "Choose access"}
    monkeypatch.setattr(assignment_store, "root_binding", lambda _: {})
    monkeypatch.setattr(question_items, "list_by_doc", lambda _: [item, {"id": 2, "answer_count": 1}])
    monkeypatch.setattr(run_service.run_store, "ai_runs_of_node", lambda *a: ["asker"])
    monkeypatch.setattr(finalize, "open_questions_raised_by_run", lambda *a: [item,
        {"id": 3, "seq": 630, "responder_state": "failed", "responder_error_code": "provider_failed"}])
    details = run_service.question_wait_details("flow", {"node_key": "card", "run_id": "r", "result_doc_id": "doc"})
    assert len(details) == 2
    assert details[0] == {"seq": 629, "state": "user_decision", "code": "user_decision_required", "message": "Choose access"}
    assert details[1]["code"] == "provider_failed"
