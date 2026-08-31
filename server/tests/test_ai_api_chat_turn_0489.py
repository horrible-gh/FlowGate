"""API-provider chat reply registration regressions for group 0489."""
from __future__ import annotations

import io
import json
import threading
import time
import urllib.error

import pytest

from modules.flow_gate.services import ai_invoke_service as svc


def _run() -> dict:
    return {
        "project_id": "flowgate", "chain_source": "system", "run_id": "aiv_chat",
        "docs_target": 1, "raw_token": "chat-token", "token_id": "tok_chat_123",
        "action_scope": "chat", "mode": "single", "doc_ref": "flowgate.default.0489.0001-CH",
        "api_base_url": "http://flowgate.test/api/v1/", "cancel_event": threading.Event(),
        "started_mono": time.monotonic(), "timeout_sec": 30,
    }


@pytest.mark.parametrize("kind, call_name", [("openai", "_call_openai"), ("claude", "_call_anthropic")])
def test_api_chat_uses_reply_tool_and_conversation_turn_not_inbox(monkeypatch, kind, call_name):
    monkeypatch.setattr(svc.ai_settings_service, "get_provider_secret", lambda *_: "key")
    seen = []

    def model(*args):
        seen.append(args[5])
        return "reply", {"id": "tc1", "name": args[5], "input": {"body": "hello"}}, {
            "role": "assistant", "content": "reply",
        }

    sent = []
    monkeypatch.setattr(svc, call_name, model)
    monkeypatch.setattr(svc, "_conversation_turn_register", lambda provider, run, token, payload: (
        sent.append((provider, run, token, payload)) or (201, {"ok": True, "head_seq": 5})
    ))
    monkeypatch.setattr(svc, "_inbox_register", lambda *_: pytest.fail("chat must not call /inbox"))

    result = svc._api_execute({"id": "aip_chat", "kind": kind, "display_name": "Provider"}, "prompt", _run())

    assert result == ("started_ok", None)
    assert seen == ["send_chat_reply"]
    assert sent[0][2:] == ("chat-token", {"body": "hello"})


def test_conversation_turn_register_binds_token_and_provider_name(monkeypatch):
    captured = {}

    class Response:
        status = 201

        def read(self):
            return b'{"ok": true}'

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["headers"] = dict(request.header_items())
        captured["body"] = json.loads(request.data.decode("utf-8"))
        assert timeout == 120
        return Response()

    monkeypatch.setattr(svc.urllib.request, "urlopen", fake_urlopen)
    status, response = svc._conversation_turn_register(
        {"display_name": "Claude API"}, _run(), "chat-token", {"body": "saved reply"},
    )

    assert (status, response) == (201, {"ok": True})
    assert captured["url"] == "http://flowgate.test/api/v1/conversation/flowgate.default.0489.0001-CH/turn"
    assert captured["body"] == {
        "body": "saved reply", "idempotency_key": "tok_chat_123", "display_name": "Claude API",
    }
    assert captured["headers"]["Authorization"] == "Bearer chat-token"


def test_conversation_turn_register_preserves_context_binding_403(monkeypatch):
    error = urllib.error.HTTPError(
        "http://flowgate.test/api/v1/conversation/wrong/turn", 403, "Forbidden", {},
        io.BytesIO(b'{"detail": "Context binding mismatch. Use the correct token."}'),
    )
    monkeypatch.setattr(svc.urllib.request, "urlopen", lambda *_, **__: (_ for _ in ()).throw(error))

    status, response = svc._conversation_turn_register({}, _run(), "wrong-token", {"body": "nope"})

    assert status == 403
    assert response["detail"] == "Context binding mismatch. Use the correct token."