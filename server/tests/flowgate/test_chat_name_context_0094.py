"""0094: conversation context and bounded naming attempts."""
import pytest
from test_flow_first_names_0071 import naming, env, db_path, legacy_cards, FA, USER, TEXT
from modules.flow_gate.db import documents
from modules.flow_gate.graph_workflow.services import first_names_store as ledger
from flowgate.conversation import auto_title as a, legacy_adapter

pytestmark = pytest.mark.usefixtures("legacy_cards")


def test_later_conversation_names_chat(naming, monkeypatch):
    monkeypatch.setattr(legacy_adapter, "first_turns", lambda *args: [
        {"speaker": "user", "body": "내용 파악하고 이야기 하자"},
        {"speaker": "ai", "body": "로그인 장애를 살펴보겠습니다"}])
    prompts = []
    def complete(project, provider, prompt, user, **kwargs):
        prompts.append(prompt)
        return "로그인 장애", None
    monkeypatch.setattr(legacy_adapter, "complete_naming", complete)
    result = a.generate_flow_names(FA, USER, "p", chat_doc_id=naming["chat"], kinds=("chat",))
    assert result["targets"]["chat"] == "done"
    assert "Later conversation:" in prompts[0] and "로그인 장애를 살펴보겠습니다" in prompts[0]
    assert documents.get_by_id(naming["chat"])["title"] == "로그인 장애"


def test_comments_removed_from_later_turns(monkeypatch):
    monkeypatch.setattr(legacy_adapter, "first_turns", lambda *args: [
        {"speaker": "user", "body": "first"},
        {"speaker": "ai", "body": "visible <!--fg-quick x-->secret<!--/fg-quick--> <!--hidden--> end"}])
    context = a._chat_context("chat")
    prompt = a.build_name_prompt("chat", "first", conversation=context)
    assert "visible" in prompt and "end" in prompt
    assert "secret" not in prompt and "hidden" not in prompt and "fg-quick" not in prompt


def test_no_title_stops_after_three_calls(naming, monkeypatch):
    calls = []
    monkeypatch.setattr(legacy_adapter, "complete_naming", lambda *args, **kwargs: (calls.append(1) or "NO_TITLE", None))
    for _ in range(5):
        a.generate_flow_names(FA, USER, "p", chat_doc_id=naming["chat"], kinds=("chat",))
    assert len(calls) == 3
    entry = ledger.load(FA)["names"]["chat:" + naming["chat"]]
    assert entry["state"] == "exhausted" and entry["attempts"] == 3


def test_unavailable_does_not_consume_attempts(naming, monkeypatch):
    calls = []
    reason = next(iter(legacy_adapter.PROMPT_UNAVAILABLE_REASONS))
    monkeypatch.setattr(legacy_adapter, "complete_naming", lambda *args, **kwargs: (calls.append(1) or None, reason))
    for _ in range(5):
        a.generate_flow_names(FA, USER, "p", chat_doc_id=naming["chat"], kinds=("chat",))
    assert len(calls) == 5
    assert ledger.load(FA)["names"]["chat:" + naming["chat"]]["attempts"] == 0


def test_legacy_chat_prompt_unchanged():
    assert a.build_name_prompt("chat", TEXT) == a.build_prompt(TEXT) + "\nIf no concrete subject can be identified, reply NO_TITLE."


@pytest.mark.parametrize("root_text", ["", "Start document subject"])
def test_flow_uses_later_conversation(naming, monkeypatch, root_text):
    naming["root_text"] = root_text
    monkeypatch.setattr(legacy_adapter, "first_turns", lambda *args: [
        {"speaker": "user", "body": "Let's discuss"},
        {"speaker": "ai", "body": "Investigate login failures"}])
    prompts = []
    monkeypatch.setattr(legacy_adapter, "complete_naming", lambda *args, **kwargs:
                        (prompts.append(args[2]) or "Login investigation", None))
    result = a.generate_flow_names(FA, USER, "p", chat_doc_id=naming["chat"], kinds=("flow",))
    assert result["targets"]["flow"] == "done"
    assert "Later conversation:" in prompts[0]
    assert "Investigate login failures" in prompts[0]
    assert "whole task" in prompts[0]
    assert "later conversation, then from the start document" in prompts[0]


@pytest.mark.parametrize("kind,change", [("flow", "root"), ("flow", "conversation"),
                                        ("flow", "first"), ("root", "root")])
def test_changed_input_renews_only_its_attempt_budget(naming, monkeypatch, kind, change):
    naming["root_text"] = "..."
    inputs = {"first": "Let's discuss", "conversation": "Still discussing"}
    monkeypatch.setattr(legacy_adapter, "first_turns", lambda *args: [
        {"speaker": "user", "body": inputs["first"]},
        {"speaker": "ai", "body": inputs["conversation"]}])
    # Keep root's unchanged heading fallback from ending this failure scenario.
    monkeypatch.setattr(a, "heading_title", lambda *args: None)
    naming["root_text"] = "Initial subject"
    calls = []
    monkeypatch.setattr(legacy_adapter, "complete_naming", lambda *args, **kwargs:
                        (calls.append(args[2]) or "NO_TITLE", None))
    def run():
        return a.generate_flow_names(FA, USER, "p", chat_doc_id=naming["chat"], kinds=(kind,))
    for _ in range(5):
        run()
    old_hash = ledger.load(FA)["names"][kind]["input_hash"]
    assert len(calls) == 3
    if change == "root":
        naming["root_text"] = "Updated subject"
    else:
        inputs[change] = "Updated subject"
    for _ in range(5):
        run()
    entry = ledger.load(FA)["names"][kind]
    assert len(calls) == 6
    assert entry["input_hash"] != old_hash
    assert entry["attempts"] == 3 and entry["state"] == "exhausted"


def test_legacy_exhausted_flow_gets_one_budget(naming, monkeypatch):
    from flowgate.conversation import first_names_ports as p
    _, targets = p.targets(FA, None, chat_only=False, root=False)
    p.guard(FA, lambda: ledger.save(FA, {"names": {"flow": {
        "id": targets["flow"]["id"], "state": "exhausted", "attempts": 3}}}))
    calls = []
    monkeypatch.setattr(legacy_adapter, "complete_naming", lambda *args, **kwargs:
                        (calls.append(1) or "NO_TITLE", None))
    for _ in range(5):
        a.generate_flow_names(FA, USER, "p", chat_doc_id=naming["chat"], kinds=("flow",))
    assert len(calls) == 3
    assert ledger.load(FA)["names"]["flow"]["attempts"] == 3


@pytest.mark.parametrize("manual", [False, True])
def test_input_change_keeps_done_or_manual_flow(naming, monkeypatch, manual):
    from modules.flow_gate.db import groups
    calls = []
    monkeypatch.setattr(legacy_adapter, "complete_naming", lambda *args, **kwargs:
                        (calls.append(1) or "Login investigation", None))
    if manual:
        groups.update(FA, {"title": "My chosen flow"})
    def run():
        return a.generate_flow_names(FA, USER, "p", chat_doc_id=naming["chat"], kinds=("flow",))
    run()
    before = ledger.load(FA)
    naming["root_text"] = "Changed requirements"
    assert run()["targets"]["flow"] == ("skipped" if manual else "done")
    assert ledger.load(FA) == before
    assert len(calls) == (0 if manual else 1)


def test_input_change_during_active_lease_waits(naming, monkeypatch):
    calls = []
    def complete(*args, **kwargs):
        calls.append(args[2])
        naming["root_text"] = "Changed while generating"
        result = a.generate_flow_names(FA, USER, "p", chat_doc_id=naming["chat"], kinds=("flow",))
        assert result["targets"]["flow"] == "unchanged"
        assert ledger.load(FA)["names"]["flow"]["attempts"] == 1
        return "NO_TITLE", None
    monkeypatch.setattr(legacy_adapter, "complete_naming", complete)
    a.generate_flow_names(FA, USER, "p", chat_doc_id=naming["chat"], kinds=("flow",))
    assert len(calls) == 1
    assert "Changed while generating" not in calls[0]
