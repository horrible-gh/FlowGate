"""First-run three-name service: real SQLite ledger/targets; only the model is fake."""
from __future__ import annotations
import json
import threading
import pytest
from test_fg_card_settings import legacy_cards, db_path, env, FA, FB, _doc
from modules.flow_gate.db import groups, documents
from modules.flow_gate.graph_workflow.services import first_names_store as ledger, root_start
from modules.flow_gate.graph_workflow.services.flow_guard import with_flow_guard
from modules.flow_gate.graph_workflow.store import title_store
from flowgate.conversation import auto_title as a, first_names_ports as p, legacy_adapter

pytestmark = pytest.mark.usefixtures("legacy_cards")
USER = {"user_id": "u_kim"}
TEXT = "로그인 오류 원인을 조사하고 수정해 주세요"
TITLE = "로그인 오류 수정"


@pytest.fixture()
def naming(env, monkeypatch):
    groups.update(FA, {"title": "새 플로우"})
    key = root_start.ensure_in_guard(FA, "u_kim", "ko")
    chat = _doc(FA, 12, "CH")
    # The common fixture returns a doc id.
    if isinstance(chat, dict):
        chat = chat["doc_id"]
    chat = chat or f"{FA}.0012-CH"
    documents.update(chat, {"title": "새 대화", "meta": a.owed_meta("새 대화")})
    state = {"key": key, "chat": chat, "calls": 0, "hook": None, "answer": TITLE}
    monkeypatch.setattr(legacy_adapter, "has_permission", lambda *args: True)
    monkeypatch.setattr(legacy_adapter, "title_max_len", lambda: 100)
    monkeypatch.setattr(legacy_adapter, "naming_remote_provider", lambda *args: False)
    def prompt(*args, **kwargs):
        state["calls"] += 1
        assert TEXT in args[2]
        if state["hook"]:
            state["hook"]()
        return state["answer"], None
    monkeypatch.setattr(legacy_adapter, "complete_prompt", prompt)
    monkeypatch.setattr(legacy_adapter, "first_turns", lambda *args: [
        {"speaker": "user", "body": state.get("message", TEXT)}, {"speaker": "ai", "body": "done"}])
    return state


def run(naming, text=TEXT):
    naming["message"] = text
    return a.generate_flow_names(FA, USER, "provider", text, naming["chat"])


def titles(n):
    st = title_store.state(FA, n["key"])
    return groups.get_by_id(FA)["title"], st["title"] if st else None, documents.get_by_id(n["chat"])["title"]


def test_three_names_once_and_durable_record(naming):
    assert run(naming) == {"status": "done", "targets": {"flow": "done", "root": "done", "chat": "done"}}
    assert titles(naming) == (TITLE, TITLE, TITLE)
    saved = ledger.load(FA)
    assert saved["candidate"] == TITLE
    assert run(naming)["status"] == "done"
    assert ledger.load(FA) == saved and naming["calls"] == 1
    assert groups.get_by_id(FB)["title"] != TITLE


@pytest.mark.parametrize("kind", ["flow", "root", "chat"])
def test_manual_default_title_before_or_during_ai_is_protected(naming, kind):
    from modules.flow_gate import process_service
    from modules.flow_gate.documents import document_service
    def human():
        if kind == "flow":
            process_service.update_group(FA, title="새 플로우")
        elif kind == "root":
            with_flow_guard(FA, lambda: title_store.save_human(FA, naming["key"], "요구사항", "u_kim"))
        else:
            document_service.update_document(naming["chat"], {"title": "새 대화"}, "u_kim")
    naming["hook"] = human
    result = run(naming)
    assert result["targets"][kind] == "skipped"
    assert titles(naming)[["flow", "root", "chat"].index(kind)] == {
        "flow": "새 플로우", "root": "요구사항", "chat": "새 대화"}[kind]
    naming["hook"] = None
    assert run(naming)["targets"][kind] == "skipped"
    assert naming["calls"] == 1


@pytest.mark.parametrize("kind", ["flow", "root", "chat"])
def test_partial_failure_rolls_back_only_one_and_retries_without_ai(naming, monkeypatch, kind):
    original = p.apply
    def fail(flow, target, *args):
        original(flow, target, *args)
        if target == kind:
            raise RuntimeError("disk_failure")
    monkeypatch.setattr(p, "apply", fail)
    result = run(naming)
    assert result["status"] == "pending"
    assert result["targets"][kind] == "pending"
    assert sum(x == "done" for x in result["targets"].values()) == 2
    successful = {k: v for k, v in ledger.load(FA)["targets"].items() if k != kind}
    monkeypatch.setattr(p, "apply", original)
    assert run(naming)["status"] == "done"
    assert titles(naming) == (TITLE, TITLE, TITLE) and naming["calls"] == 1
    assert {k: v for k, v in ledger.load(FA)["targets"].items() if k != kind} == successful


@pytest.mark.parametrize("text", ["", "  ", "...!!!"])
def test_no_content_keeps_defaults_without_model(naming, text, monkeypatch):
    monkeypatch.setattr(a, "_first_user_message", lambda _: None)
    assert run(naming, text)["status"] == "unchanged"
    assert titles(naming) == ("새 플로우", None, "새 대화")
    assert naming["calls"] == 0


@pytest.mark.parametrize("answer", [None, "", "NO_TITLE", '""'])
def test_empty_or_insufficient_answer_never_saves(naming, answer):
    naming["answer"] = answer
    assert run(naming)["status"] == "unchanged"
    assert titles(naming) == ("새 플로우", None, "새 대화")


def test_concurrent_trigger_does_not_generate_twice(naming):
    observed = []
    naming["hook"] = lambda: observed.append(run(naming))
    assert run(naming)["status"] == "done"
    assert observed == [{"status": "unchanged"}] and naming["calls"] == 1


def test_no_chat_does_not_create_one(naming):
    result = a.generate_flow_names(FA, USER, "provider", TEXT)
    assert result["targets"] == {"flow": "done", "root": "done"}
    assert documents.get_by_id(naming["chat"])["title"] == "새 대화"


def test_custom_titles_stay_and_no_model_when_all_custom(naming):
    groups.update(FA, {"title": "내 플로우"})
    with_flow_guard(FA, lambda: title_store.save_human(FA, naming["key"], "내 시작 카드", "u_kim"))
    documents.update(naming["chat"], {"title": "내 대화"})
    assert run(naming)["status"] == "unchanged"
    assert titles(naming) == ("내 플로우", "내 시작 카드", "내 대화")
    assert naming["calls"] == 0


def test_wrong_chat_scope_and_permission_refuse_without_ai(naming, monkeypatch):
    assert a.generate_flow_names(FB, USER, "provider", TEXT, naming["chat"])["status"] == "failed"
    monkeypatch.setattr(legacy_adapter, "has_permission", lambda *args: False)
    assert run(naming)["status"] == "failed"
    assert naming["calls"] == 0


def test_provider_failure_is_contained(naming, monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("provider_failed")
    monkeypatch.setattr(legacy_adapter, "complete_prompt", fail)
    assert run(naming)["status"] == "failed"
    assert titles(naming) == ("새 플로우", None, "새 대화")


def test_background_entry_returns_before_model_finishes(monkeypatch):
    started, release, done = threading.Event(), threading.Event(), threading.Event()
    def blocked(*args):
        started.set()
        release.wait(5)
        done.set()
    monkeypatch.setattr(a, "generate_flow_names", blocked)
    try:
        assert a.schedule_flow_names("flow", USER, "provider", TEXT)
        assert started.wait(2) and not done.is_set()
    finally:
        release.set()
        assert done.wait(2)


def test_background_saturation_is_immediate(monkeypatch):
    monkeypatch.setattr(a, "_first_names_slots", threading.BoundedSemaphore(0))
    assert a.schedule_flow_names("flow", USER, "provider", TEXT) is False


def test_bug_root_uses_card_one_default(naming):
    _doc(FB, 1, "B")
    groups.update(FB, {"title": "새 플로우"})
    key = root_start.ensure_in_guard(FB, "u_kim", "ko")
    assert a.generate_flow_names(FB, USER, "provider", TEXT)["status"] == "done"
    assert title_store.state(FB, key)["title"] == TITLE
    assert groups.get_by_id(FA)["title"] == "새 플로우"


def test_manual_before_first_trigger_and_after_done(naming):
    from modules.flow_gate import process_service
    from modules.flow_gate.documents import document_service
    process_service.update_group(FA, title="새 플로우")
    document_service.update_document(naming["chat"], {"title": "새 대화"}, "u_kim")
    assert run(naming)["targets"] == {"flow": "skipped", "root": "done", "chat": "skipped"}
    with_flow_guard(FA, lambda: title_store.save_human(FA, naming["key"], "나의 카드", "u_kim"))
    assert run(naming)["status"] == "done"
    assert titles(naming) == ("새 플로우", "나의 카드", "새 대화")


def test_a_bound_custom_document_title_is_preserved(naming, monkeypatch):
    from types import SimpleNamespace
    doc_id = f"{FA}.0001-R"
    documents.update(doc_id, {"title": "사용자가 정한 요구사항"})
    row = {"node_key": naming["key"], "node_type": "document", "doc_type": "R", "label": "요구사항"}
    view = SimpleNamespace(nodes=[row], bound_node_key=naming["key"], bound_doc_id=doc_id, node_runs={})
    monkeypatch.setattr(p, "title_views", lambda _: ([view], {doc_id: documents.get_by_id(doc_id)}))
    assert run(naming)["targets"]["root"] == "skipped"
    assert title_store.state(FA, naming["key"]) is None


def test_ledger_failure_rolls_back_title_and_retry_reuses_candidate(naming, monkeypatch):
    save = ledger.save
    def fail(flow, record):
        if record.get("targets", {}).get("flow", {}).get("state") == "done":
            raise RuntimeError("ledger_failed")
        save(flow, record)
    monkeypatch.setattr(ledger, "save", fail)
    assert run(naming)["targets"]["flow"] == "pending"
    assert groups.get_by_id(FA)["title"] == "새 플로우"
    monkeypatch.setattr(ledger, "save", save)
    assert run(naming)["status"] == "done" and naming["calls"] == 1


def test_missing_chat_after_claim_does_not_block_other_names(naming):
    naming["hook"] = lambda: documents.delete(naming["chat"])
    result = run(naming)
    assert result["targets"] == {"flow": "done", "root": "done", "chat": "pending"}


def test_expired_generation_lease_can_recover(naming, monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("worker_lost")
    complete = legacy_adapter.complete_prompt
    monkeypatch.setattr(legacy_adapter, "complete_prompt", fail)
    assert run(naming)["status"] == "failed"
    def expire():
        state = ledger.load(FA)
        state["lease_until"] = 0
        ledger.save(FA, state)
    with_flow_guard(FA, expire)
    monkeypatch.setattr(legacy_adapter, "complete_prompt", complete)
    assert a.generate_flow_names(FA, USER, "provider", TEXT, naming["chat"], title=TITLE)["status"] == "done"
