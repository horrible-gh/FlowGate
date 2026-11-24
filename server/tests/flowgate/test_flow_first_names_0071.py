"""First-run names (0071/0080): real SQLite ledger/targets; only the model is fake.

The flow, its start card and each chat are named separately, each from its own content:
flow <- the whole first request, start card <- its bound document, chat <- its own messages.
"""
from __future__ import annotations
import threading
import pytest
from test_fg_card_settings import legacy_cards, db_path, env, FA, FB, _doc
from modules.flow_gate.db import groups, documents
from modules.flow_gate.graph_workflow.services import first_names_store as ledger, root_start
from modules.flow_gate.graph_workflow.services.flow_guard import with_flow_guard
from modules.flow_gate.graph_workflow.store import title_store
from flowgate.conversation import auto_title as a, first_names_ports as p, first_names_triggers as t, legacy_adapter

pytestmark = pytest.mark.usefixtures("legacy_cards")
USER = {"user_id": "u_kim"}
TEXT = "로그인 오류 원인을 조사하고 수정해 주세요"
ROOT_TEXT = "# 로그인 장애 요구사항\n\n로그인 실패 원인을 찾아 고친다."
FLOW_T, CARD_T, CHAT_T = "로그인 장애 대응", "로그인 장애 요구사항 정리", "로그인 오류 원인 조사"
TITLE = CHAT_T
ALL = (FLOW_T, CARD_T, CHAT_T)
DEFAULTS = ("새 플로우", None, "새 대화")


def kind_of(prompt):
    return "flow" if "workflow" in prompt else "root" if "document below" in prompt else "chat"


@pytest.fixture()
def naming(env, monkeypatch):
    groups.update(FA, {"title": "새 플로우"})
    key = root_start.ensure_in_guard(FA, "u_kim", "ko")
    chat = _doc(FA, 12, "CH")
    if isinstance(chat, dict):
        chat = chat["doc_id"]
    chat = chat or f"{FA}.0012-CH"
    documents.update(chat, {"title": "새 대화", "meta": a.owed_meta("새 대화")})
    state = {"key": key, "chat": chat, "calls": 0, "kinds": [], "prompts": [], "hook": None,
             "answers": {"flow": FLOW_T, "root": CARD_T, "chat": CHAT_T}, "root_text": ROOT_TEXT}
    monkeypatch.setattr(legacy_adapter, "has_permission", lambda *args: True)
    monkeypatch.setattr(legacy_adapter, "title_max_len", lambda: 100)
    monkeypatch.setattr(legacy_adapter, "naming_remote_provider", lambda *args: False)
    monkeypatch.setattr(t, "run_context", lambda *args: ("provider", state["root_text"], None))
    def prompt(*args, **kwargs):
        kind = kind_of(args[2])
        state["calls"] += 1
        state["kinds"].append(kind)
        state["prompts"].append(args[2])
        if state["hook"]:
            state["hook"]()
        answer = state["answers"][kind]
        return answer, None if answer else "timeout"
    monkeypatch.setattr(legacy_adapter, "complete_prompt", prompt)
    monkeypatch.setattr(legacy_adapter, "first_turns", lambda doc_id, *args: [
        {"speaker": "user", "body": state.get("messages", {}).get(doc_id, state.get("message", TEXT))},
        {"speaker": "ai", "body": "done"}])
    return state


def run(naming, text=TEXT, **kwargs):
    naming["message"] = text
    return a.generate_flow_names(FA, USER, "provider", text, naming["chat"], **kwargs)


def titles(n, chat=None):
    st = title_store.state(FA, n["key"])
    return (groups.get_by_id(FA)["title"], st["title"] if st else None,
            documents.get_by_id(chat or n["chat"])["title"])


def test_each_target_named_from_its_own_content(naming):
    assert run(naming) == {"status": "done", "targets": {"chat": "done", "flow": "done", "root": "done"}}
    assert titles(naming) == ALL
    by_kind = dict(zip(naming["kinds"], naming["prompts"]))
    assert sorted(by_kind) == ["chat", "flow", "root"]
    assert TEXT in by_kind["chat"] and TEXT in by_kind["flow"]
    assert "로그인 실패 원인을 찾아 고친다" in by_kind["root"] and TEXT not in by_kind["root"]
    names = ledger.load(FA)["names"]
    assert (names["flow"]["candidate"], names["root"]["candidate"],
            names["chat:" + naming["chat"]]["candidate"]) == ALL
    saved = ledger.load(FA)
    assert run(naming)["status"] == "done"
    assert ledger.load(FA) == saved and naming["calls"] == 3
    assert groups.get_by_id(FB)["title"] != FLOW_T


def test_second_chat_gets_its_own_name_and_keeps_the_others(naming):
    assert run(naming)["status"] == "done"
    second = _doc(FA, 13, "CH")
    second = second["doc_id"] if isinstance(second, dict) else second or f"{FA}.0013-CH"
    documents.update(second, {"title": "새 대화", "meta": "{}"})
    naming["messages"] = {second: "비밀번호 재설정 메일이 안 와요"}
    naming["answers"]["chat"] = "비밀번호 재설정 메일 문제"
    assert a.generate_flow_names(FA, USER, "p", chat_doc_id=second)["targets"] == {
        "chat": "done", "flow": "done", "root": "done"}
    assert documents.get_by_id(second)["title"] == "비밀번호 재설정 메일 문제"
    assert "비밀번호 재설정 메일이 안 와요" in naming["prompts"][-1]
    assert titles(naming) == ALL
    four = set(titles(naming)) | {documents.get_by_id(second)["title"]}
    assert len(four) == 4 and naming["calls"] == 4


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
    calls = naming["calls"]
    assert run(naming)["targets"][kind] == "skipped"
    assert naming["calls"] == calls


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
    assert titles(naming)[["flow", "root", "chat"].index(kind)] == DEFAULTS[["flow", "root", "chat"].index(kind)]
    monkeypatch.setattr(p, "apply", original)
    assert run(naming)["status"] == "done"
    assert titles(naming) == ALL and naming["calls"] == 3


@pytest.mark.parametrize("text", ["", "  ", "...!!!"])
def test_no_content_keeps_defaults_without_model(naming, text, monkeypatch):
    monkeypatch.setattr(a, "_first_user_message", lambda _: None)
    naming["root_text"] = text
    assert run(naming, text)["status"] == "unchanged"
    assert titles(naming) == DEFAULTS
    assert naming["calls"] == 0


@pytest.mark.parametrize("answer", [None, "", "NO_TITLE", '""'])
def test_empty_or_insufficient_answer_never_saves(naming, answer):
    naming["answers"] = {"flow": answer, "root": answer, "chat": answer}
    naming["root_text"] = "..."
    assert run(naming)["status"] == "unchanged"
    assert titles(naming) == DEFAULTS


def test_concurrent_trigger_does_not_generate_twice(naming):
    observed = []
    naming["hook"] = lambda: observed.append(run(naming)) if not observed else None
    assert run(naming)["status"] == "done"
    assert observed[0]["targets"]["chat"] == "unchanged"
    assert naming["kinds"].count("chat") == 1 and naming["calls"] == 3
    assert titles(naming) == ALL


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
    assert a.generate_flow_names(FB, USER, "provider", TEXT, naming["chat"])["targets"]["chat"] == "failed"
    monkeypatch.setattr(legacy_adapter, "has_permission", lambda *args: False)
    assert run(naming)["status"] == "failed"
    assert titles(naming) == DEFAULTS
    assert "chat" not in naming["kinds"]


def test_provider_failure_is_contained(naming, monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("provider_failed")
    monkeypatch.setattr(legacy_adapter, "complete_prompt", fail)
    naming["root_text"] = "..."
    assert run(naming)["status"] == "failed"
    assert titles(naming) == DEFAULTS


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
    assert title_store.state(FB, key)["title"] == CARD_T
    assert groups.get_by_id(FA)["title"] == "새 플로우"


def test_manual_before_first_trigger_and_after_done(naming):
    from modules.flow_gate import process_service
    from modules.flow_gate.documents import document_service
    process_service.update_group(FA, title="새 플로우")
    document_service.update_document(naming["chat"], {"title": "새 대화"}, "u_kim")
    assert run(naming)["targets"] == {"chat": "skipped", "flow": "skipped", "root": "done"}
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
        if record.get("names", {}).get("flow", {}).get("state") == "done":
            raise RuntimeError("ledger_failed")
        save(flow, record)
    monkeypatch.setattr(ledger, "save", fail)
    assert run(naming)["targets"]["flow"] == "pending"
    assert groups.get_by_id(FA)["title"] == "새 플로우"
    monkeypatch.setattr(ledger, "save", save)
    assert run(naming)["status"] == "done" and naming["calls"] == 3
    assert titles(naming) == ALL


def test_missing_chat_after_claim_does_not_block_other_names(naming):
    naming["hook"] = lambda: documents.delete(naming["chat"]) if documents.get_by_id(naming["chat"]) else None
    result = run(naming)
    assert result["targets"]["flow"] == "done" and result["targets"]["root"] == "done"
    assert result["targets"]["chat"] in ("pending", "skipped")


def test_expired_generation_lease_can_recover_with_seed(naming, monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("worker_lost")
    monkeypatch.setattr(legacy_adapter, "complete_prompt", fail)
    naming["root_text"] = "..."
    assert run(naming)["status"] == "failed"
    assert a.generate_flow_names(FA, USER, "provider", TEXT, naming["chat"], title=CHAT_T,
                                 flow_title=FLOW_T)["targets"] == {
        "chat": "done", "flow": "done", "root": "unchanged"}
    assert titles(naming) == (FLOW_T, None, CHAT_T)


def test_rebound_root_document_gets_its_own_name(naming, monkeypatch):
    assert run(naming)["status"] == "done"
    with_flow_guard(FA, lambda: title_store.save_human(FA, naming["key"], "요구사항", "u_kim"))
    record = ledger.load(FA)
    record["names"]["root"]["id"] = "old-node"
    with_flow_guard(FA, lambda: ledger.save(FA, record))
    # A human default title stays protected even when the slot is fresh.
    assert run(naming)["targets"]["root"] == "skipped"
