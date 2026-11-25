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
