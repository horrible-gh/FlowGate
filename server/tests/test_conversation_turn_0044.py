"""Compatibility coverage for the session conversation endpoint.

Group 0351 moved persistence from whole-file append to the shared append-only DB
service.  Detailed behavior now lives in test_conversation_append_0351.py; this file
keeps the original 0044 regression target while asserting the new route ownership and
required idempotency contract.
"""
from __future__ import annotations

from unittest.mock import patch

from fastapi import FastAPI
from starlette.testclient import TestClient

from modules.flow_gate.auth.middleware import get_current_user
from modules.flow_gate.documents.routers import conversation_turns
from modules.flow_gate.services import conversation_turn_service

DOC_ID = "testprj-__ALL__-0044-CH0002"


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(conversation_turns.router)
    app.dependency_overrides[get_current_user] = lambda: {
        "user_id": "usr_test_001", "username": "owner"
    }
    return TestClient(app)


def _result() -> dict:
    return {
        "ok": True, "doc_id": DOC_ID, "replayed": False, "head_seq": 1,
        "turn": {
            "seq": 1, "speaker": "user", "participant_key": "user:usr_test_001",
            "display_name": "owner", "locale": "ko", "body": "hello",
            "based_on_seq": 0, "stale_since_seq": None, "source_run_id": None,
            "created_at": "2026-01-01T00:00:00+09:00",
        },
        "me": {
            "participant_key": "user:usr_test_001", "kind": "user",
            "display_name": "owner", "first_seen_seq": 1, "last_read_seq": 0,
            "last_written_seq": 1, "last_seen_at": "2026-01-01T00:00:00+09:00",
        },
    }


def test_turn_endpoint_requires_idempotency_key():
    response = _client().post(
        f"/documents/{DOC_ID}/conversation/turn", json={"body": "hello"}
    )
    assert response.status_code == 422


def test_turn_endpoint_forces_session_speaker_and_returns_turn_object():
    with patch.object(conversation_turn_service, "append_turn", return_value=_result()) as append:
        response = _client().post(
            f"/documents/{DOC_ID}/conversation/turn",
            json={"body": "hello", "speaker": "ai", "idempotency_key": "sess_12345678"},
        )
    assert response.status_code == 201
    assert response.json()["turn"]["speaker"] == "user"
    assert append.call_args.kwargs["actor"]["kind"] == "session"
    assert "content" not in response.json()


def test_empty_body_is_a_domain_422():
    with patch.object(
        conversation_turn_service,
        "append_turn",
        side_effect=conversation_turn_service.ConversationTurnError(
            422, "Turn body must not be empty."
        ),
    ):
        response = _client().post(
            f"/documents/{DOC_ID}/conversation/turn",
            json={"body": "   ", "idempotency_key": "sess_12345678"},
        )
    assert response.status_code == 422
    assert response.json()["detail"] == "Turn body must not be empty."