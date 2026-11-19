"""API re-request (nudge) keeps the "what to do now" block last (v02test.0049 TR0018)."""
from __future__ import annotations

import copy
import os
import sys
import threading
import time
from pathlib import Path

import pytest
from terminal_prompt_assertions import assert_terminal

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from modules.flow_gate.services import ai_invoke_service as svc  # noqa: E402
from modules.flow_gate.services import chat_command_service, terminal_prompt as terminal  # noqa: E402

PROVIDER = {"id": "provider", "kind": "openai", "api_base_url": "https://api.example", "api_model": "test"}
LOCALES = ["ko", "en", "ja"]


def _chat_run(locale):
    return {
        "project_id": "flowgate", "run_id": "aiv_nudge", "docs_target": 0, "raw_token": "raw-token",
        "token_id": "tok_nudge", "doc_ref": "flowgate.default.0018.0001-B", "action_scope": "chat",
        "mode": "single", "cancel_event": threading.Event(), "issued_to": "usr_admin",
        "provider": {"name": "Nudge Test"}, "api_base_url": "http://127.0.0.1:8089/flowgate/api/v1",
        "timed_out": False, "locale": locale,
    }


def _card_run(locale):
    return {
        "project_id": "flowgate", "chain_source": "system", "run_id": "aiv_card_nudge", "docs_target": 1,
        "raw_token": "doc-token", "action_scope": "new", "mode": "single", "locale": locale,
        "cancel_event": threading.Event(), "started_mono": time.monotonic(), "timeout_sec": 30,
    }


def _model(calls, final_tool):
    """Two answers without a tool, then the completion tool; snapshots every request."""
    def call(*args):
        calls.append(copy.deepcopy(args[3]))
        if len(calls) < 3:
            return "plain answer", None, {"role": "assistant", "content": "plain answer"}
        return "done", final_tool, {"role": "assistant", "content": "done", "tool_calls": []}
    return call


def _check_nudged_requests(calls, locale, expected_spec):
    assert len(calls) == 3
    block = terminal.render(expected_spec)
    for request in calls[1:]:
        last = request[-1]
        assert last["role"] == "user"
        assert last["content"].endswith(block)
        prefix = assert_terminal(last["content"])  # block last, exactly once, five lines
        assert prefix
        assert expected_spec["locale"] == locale
        # Earlier messages are history: the first request's messages are kept as they were.
        assert request[:len(calls[0])] == calls[0]
    return [assert_terminal(request[-1]["content"]) for request in calls[1:]]


def _run_chat(monkeypatch, locale, *, can_write, may_run):
    calls = []
    monkeypatch.setattr(chat_command_service, "token_allows_commands", lambda run: may_run)
    monkeypatch.setattr(svc.ai_settings_service, "get_provider_secret", lambda *_: "key")
    monkeypatch.setattr(svc, "_remaining_sec", lambda _run: 60)
    monkeypatch.setattr(svc, "_conversation_context", lambda *_: (200, {
        "head_seq": 1, "turns": [{"seq": 1, "role": "user", "body": "hello"}]}))
    monkeypatch.setattr(svc, "_conversation_turn_register", lambda run, token, payload: (201, {"ok": True}))
    monkeypatch.setattr(svc, "_inbox_register", lambda *_: pytest.fail("chat must not call /inbox"))
    tool = {"id": "c3", "name": "send_chat_reply", "input": {"body": "reply"}}
    monkeypatch.setattr(svc, "_call_openai", _model(calls, tool))
    prompt = terminal.prepare("prompt", kind="chat", locale=locale, can_write=can_write)
    run = _chat_run(locale)
    assert svc._api_execute(PROVIDER, prompt, run) == ("started_ok", None)
    assert run["tool_call_misses"] == 2
    return calls, run


@pytest.mark.parametrize("locale", LOCALES)
def test_read_only_chat_nudges_end_with_block_and_never_say_change(monkeypatch, locale):
    calls, _ = _run_chat(monkeypatch, locale, can_write=False, may_run=False)
    spec = terminal.specification("chat", locale, unanswered=False, can_write=False)
    for prefix in _check_nudged_requests(calls, locale, spec):
        assert "Use the available tools to inspect the bound work" in prefix
        assert "change" not in prefix


@pytest.mark.parametrize("locale", LOCALES)
def test_edit_chat_nudges_end_with_block_and_keep_inspect_or_change(monkeypatch, locale):
    calls, _ = _run_chat(monkeypatch, locale, can_write=True, may_run=True)
    spec = terminal.specification("chat", locale, unanswered=False, can_write=True)
    for prefix in _check_nudged_requests(calls, locale, spec):
        assert "Use the available tools to inspect or change the bound work" in prefix


@pytest.mark.parametrize("locale", LOCALES)
def test_single_tool_card_nudges_end_with_block(monkeypatch, locale):
    calls = []
    monkeypatch.setattr(svc.ai_settings_service, "get_provider_secret", lambda *_: "key")
    monkeypatch.setattr(svc, "_inbox_register", lambda *a: (200, {"ok": True, "doc_id": "d"}))
    tool = {"id": "tc3", "name": "register_document",
            "input": {"doc_type": "TR", "title": "done", "content": "body"}}
    monkeypatch.setattr(svc, "_call_openai", _model(calls, tool))
    run = _card_run(locale)
    assert svc._api_execute(PROVIDER, terminal.prepare("prompt", kind="new", locale=locale), run) == ("started_ok", None)
    assert run["tool_call_misses"] == 2
    spec = terminal.specification("new", locale)
    for prefix in _check_nudged_requests(calls, locale, spec):
        assert "Call the `register_document` tool now" in prefix


def test_nudge_message_is_the_only_new_message_and_tool_replies_stay_untouched(monkeypatch):
    calls, _ = _run_chat(monkeypatch, "en", can_write=False, may_run=False)
    # request 2 = request 1 + assistant reply + nudge; request 3 adds one more pair.
    assert len(calls[1]) == len(calls[0]) + 2
    assert len(calls[2]) == len(calls[1]) + 2
    assert [m["role"] for m in calls[2][-4:]] == ["assistant", "user", "assistant", "user"]
    assert calls[2][-4]["content"] == "plain answer"
    assert "What to do now" not in calls[2][-4]["content"]
