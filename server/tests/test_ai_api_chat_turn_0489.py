"""Regression coverage for API-provider chat reply registration (0489 TR0010)."""
from __future__ import annotations

import io
import os
import sys
import threading
import time
from pathlib import Path

import pytest

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from modules.flow_gate.services import ai_invoke_service as svc  # noqa: E402


def _run(kind="openai"):
    return {
        "project_id": "flowgate", "run_id": "aiv_test", "docs_target": 0,
        "raw_token": "raw-token", "token_id": "tok_0489", "doc_ref": "flowgate.default.0489.0001-B",
        "action_scope": "chat", "mode": "single", "cancel_event": threading.Event(),
        "provider": {"name": "Deepinfra Test"}, "api_base_url": "http://127.0.0.1:8089/flowgate/api/v1", "timed_out": False,
    }


@pytest.mark.parametrize("kind", ["openai", "claude"])
def test_api_chat_uses_reply_tool_and_conversation_registration(monkeypatch, kind):
    seen = {}
    monkeypatch.setattr(svc.ai_settings_service, "get_provider_secret", lambda *_: "key")
    monkeypatch.setattr(svc, "_remaining_sec", lambda _run: 60)
    # 0505 T0006 (DB0005 3.3): _conversation_context now returns (status, body) like its
    # five self-HTTP siblings, not a bare dict.
    monkeypatch.setattr(svc, "_conversation_context", lambda *_: (200, {
        "head_seq": 1,
        "turns": [{"seq": 1, "role": "user", "body": "are you speak korean?"}],
    }))

    def fake_call(*args):
        seen["conversation"] = args[3]
        seen["tool_name"] = args[-4]
        seen["force_tool"] = args[-1]
        return "Korean reply", {"id": "call_1", "input": {"body": "Korean reply"}}, {"role": "assistant"}

    monkeypatch.setattr(svc, "_call_anthropic" if kind == "claude" else "_call_openai", fake_call)
    monkeypatch.setattr(svc, "_conversation_turn_register", lambda run, token, payload: (
        seen.update({"turn": (run, token, payload)}) or (201, {"ok": True})
    ))
    monkeypatch.setattr(svc, "_inbox_register", lambda *_: pytest.fail("chat must not call /inbox"))

    assert svc._api_execute({"id": "provider", "kind": kind, "api_base_url": "https://api.example", "api_model": "test"}, "prompt", _run()) == ("started_ok", None)
    assert seen["tool_name"] == "send_chat_reply"
    assert seen["force_tool"] is True
    assert seen["turn"][0]["_chat_based_on_seq"] == 1
    assert seen["conversation"][0]["role"] == "system"
    assert "are you speak korean?" in seen["conversation"][2]["content"]
    assert seen["turn"][1:] == ("raw-token", {"body": "Korean reply"})


def test_chat_returns_context_unavailable_when_context_cannot_be_loaded(monkeypatch):
    monkeypatch.setattr(svc.ai_settings_service, "get_provider_secret", lambda *_: "key")
    # 0505 T0006 (DB0005 3.3): _conversation_context now returns (status, body); a
    # transport failure surfaces as (0, {"error": ...}) like its five self-HTTP siblings.
    monkeypatch.setattr(svc, "_conversation_context", lambda *_: (0, {"error": "boom"}))

    run = _run()
    assert svc._api_execute(
        {"id": "provider", "kind": "openai", "api_base_url": "https://api.example", "api_model": "test"},
        "prompt",
        run,
    ) == ("api_error", "conversation_context_unavailable")
    # DB0005 3.3: the hop's only self-HTTP attempt still lands in the three diagnostic
    # columns even though the hop itself ends before the turn loop.
    assert run["last_tool_name"] == "conversation_context"
    assert run["last_tool_status"] == 0
    assert run["last_tool_error"] == "boom"


def test_conversation_turn_request_binds_token_and_provider(monkeypatch):
    captured = {}

    class Response:
        status = 201
        def read(self):
            return b'{"ok": true}'
        def __enter__(self):
            return self
        def __exit__(self, *_):
            return False

    def fake_open(request, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setattr(svc.urllib.request, "urlopen", fake_open)
    run = _run()
    run["_chat_based_on_seq"] = 7
    status, payload = svc._conversation_turn_register(run, "raw-token", {"body": "reply"})

    request = captured["request"]
    assert (status, payload, captured["timeout"]) == (201, {"ok": True}, 120)
    assert request.full_url == "http://127.0.0.1:8089/flowgate/api/v1/conversation/flowgate.default.0489.0001-B/turn"
    assert request.get_header("Authorization") == "Bearer raw-token"
    assert request.data == b'{"body": "reply", "idempotency_key": "tok_0489", "based_on_seq": 7, "display_name": "Deepinfra Test"}'


@pytest.mark.parametrize("kind", ["openai", "claude"])
def test_chat_forces_provider_tool_choice(monkeypatch, kind):
    captured = {}

    def fake_post(url, headers, body, timeout):
        captured["body"] = body
        if kind == "claude":
            return {"content": [{"type": "tool_use", "id": "call_1", "name": "send_chat_reply", "input": {"body": "reply"}}]}
        return {"choices": [{"message": {"role": "assistant", "tool_calls": [{
            "id": "call_1", "type": "function",
            "function": {"name": "send_chat_reply", "arguments": '{"body":"reply"}'},
        }]}}]}

    monkeypatch.setattr(svc, "_http_post_json", fake_post)
    call = svc._call_anthropic if kind == "claude" else svc._call_openai
    call("https://api.example", "model", "key", [{"role": "user", "content": "reply"}],
         60, "send_chat_reply", "reply", {"type": "object"}, True)

    expected = (
        {"type": "tool", "name": "send_chat_reply"} if kind == "claude"
        else {"type": "function", "function": {"name": "send_chat_reply"}}
    )
    assert captured["body"]["tool_choice"] == expected