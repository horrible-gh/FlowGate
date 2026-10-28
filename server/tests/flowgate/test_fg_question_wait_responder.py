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
    stored = {"instruction": True, "run_ai": "worker", "instruction_ai": "worker", "review_ai": "reviewer"}
    chain = [{"id": "worker"}, {"id": "reviewer"}, {"id": "third"}]
    item = {"id": 518, "anchor_doc_id": "flowgate.v02.0110.0001-B", "answer_count": 0}
    calls, failed = [], []
    monkeypatch.setattr(wait.repository, "values", lambda *a: stored)
    monkeypatch.setattr(wait.legacy, "project_of", lambda fid: "flowgate")
    monkeypatch.setattr(wait.legacy, "default_reviewer", lambda pid: "worker")
    monkeypatch.setattr(wait.legacy, "flow_ai_busy", lambda fid: False)
    monkeypatch.setattr(review.ai_settings_service, "resolve_effective", lambda pid: {"providers": chain})
    monkeypatch.setattr(wait.legacy, "ask_responder", lambda *a: calls.append(a) or {"run_id": "answer-run"})
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
