"""Focused current-mode prompt regressions (v02test.0095)."""
import pytest

from modules.flow_gate.services import invoke_mention_service as ims
from modules.flow_gate.services.prompt_blocks.registry import brief


@pytest.mark.parametrize("locale,title", [("ko", "현재 모드"), ("en", "Current mode"), ("ja", "現在のモード")])
@pytest.mark.parametrize("source,command,claimed,label", [
    ("read", None, False, "읽기"),
    ("read", "execute", False, "실행"),
    ("read_write", None, False, "편집"),
    ("read_write", None, True, "편1"),
    ("read", None, True, "읽기"),
])
def test_block_grants(locale, title, source, command, claimed, label):
    text = brief("chat.current_mode", source_access=source, command_access=command,
                 one_shot_claimed=claimed, locale=locale)
    assert text.count("## " + title) == 1
    if locale == "ko":
        assert text.splitlines()[2] == label
        assert "모드를 바꾸라고 요구하지 말고" in text
        assert "한 줄로 한 번만" in text
        assert ("이번 한 번만" in text) == (label == "편1")
    assert ("chat_command_execute_mode_denied" in text) == (label == "실행")


@pytest.fixture
def context(monkeypatch):
    from modules.flow_gate.services import question_policy, prompt_selection, message_image_delivery
    from modules.flow_gate.graph_workflow.services import first_names_hooks
    monkeypatch.setattr(ims.chat_settings_service, "resolve_chat_settings_safe", lambda user: {})
    monkeypatch.setattr(ims.chat_doc_settings_service, "context_window", lambda *a, **kw: {"after_seq": 0, "folded": 0})
    monkeypatch.setattr(ims, "_group_title", lambda group: None)
    monkeypatch.setattr(ims, "_chat_lookup_sections", lambda **kw: [])
    monkeypatch.setattr(question_policy, "text", lambda user: "")
    monkeypatch.setattr(prompt_selection, "chat_situation", lambda *a, **kw: prompt_selection.ChatSituation(False, False, False))
    monkeypatch.setattr(message_image_delivery, "copy_notice", lambda ctx: "")
    monkeypatch.setattr(first_names_hooks, "title_request", lambda *a: "")
    return dict(doc_id="p.m.0001.0001-CH", project="p", module="m", group_name="p.m.0001",
                raw_token="test-token", token_id="test-id", user_id="test-user",
                api_base_url="http://h/flowgate/api/v1", locale="ko")


@pytest.mark.parametrize("invoked", [False, True])
@pytest.mark.parametrize("source,command,claimed,label", [
    ("read", None, False, "읽기"), ("read", "execute", False, "실행"),
    ("read_write", None, False, "편집"), ("read_write", None, True, "편1"),
    ("read", None, False, "읽기"),
])
def test_complete_prompt(context, invoked, source, command, claimed, label):
    text = ims.build_conversation_mention(**context, source_access=source, command_access=command,
                                         one_shot_claimed=claimed, command_execution=invoked)
    assert text.count("## 현재 모드") == 1
    assert "## 현재 모드\n---\n" + label + "\n" in text
    assert "## Execute mode" not in text
    assert ("이번 한 번만" in text) == (label == "편1")


@pytest.mark.parametrize("marker,label", [("test-id", "편1"), ("other-token", "편집"), (None, "편집")])
def test_invoked_claim_uses_token_identity(context, monkeypatch, marker, label):
    from modules.flow_gate.db import user_chat_source_access
    monkeypatch.setattr(user_chat_source_access, "get", lambda *a: {
        "source_access_mode": "read_only", "one_shot_token_id": marker})
    text = ims.build_conversation_mention(**context, source_access="read_write", command_execution=True)
    assert "## 현재 모드\n---\n" + label + "\n" in text


def test_lost_claim_does_not_read_preference(context, monkeypatch):
    from modules.flow_gate.db import user_chat_source_access
    def forbidden(*a):
        raise AssertionError("read grant must not consult requested mode")
    monkeypatch.setattr(user_chat_source_access, "get", forbidden)
    text = ims.build_conversation_mention(**context, source_access="read", command_execution=True)
    assert "## 현재 모드\n---\n읽기\n" in text
    assert "이번 한 번만" not in text
