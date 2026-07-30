"""Session adapter and shared append contract tests for group 0351."""
from __future__ import annotations

from unittest.mock import patch

from fastapi import FastAPI
from starlette.testclient import TestClient

from modules.flow_gate.auth.middleware import get_current_user
from modules.flow_gate.documents.routers import conversation_turns as router_module
from modules.flow_gate.services import conversation_turn_service as service


def _result(replayed: bool = False) -> dict:
    return {
        "ok": True,
        "doc_id": "flowgate.default.0351.0002-CH",
        "replayed": replayed,
        "head_seq": 1,
        "turn": {
            "seq": 1, "speaker": "user", "participant_key": "user:u1",
            "display_name": "User", "locale": "ko", "body": "hello",
            "based_on_seq": 0, "stale_since_seq": None, "source_run_id": None,
            "created_at": "2026-01-01T00:00:00+09:00",
        },
        "me": {
            "participant_key": "user:u1", "kind": "user", "display_name": "User",
            "first_seen_seq": 1, "last_read_seq": 0, "last_written_seq": 1,
            "last_seen_at": "2026-01-01T00:00:00+09:00",
        },
    }


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(router_module.router)
    app.dependency_overrides[get_current_user] = lambda: {
        "user_id": "u1", "username": "User"
    }
    return TestClient(app)


def test_body_normalization_is_nfc_lf_and_trailing_space_stable():
    assert service.normalize_body("  가  \r\nline  \r\n") == "가\nline"


def test_session_adapter_uses_shared_service_and_server_actor_identity():
    with patch.object(service, "append_turn", return_value=_result()) as append:
        response = _client().post(
            "/documents/flowgate.default.0351.0002-CH/conversation/turn",
            headers={"X-Locale": "ko-KR"},
            json={
                "body": "hello", "speaker": "ai",
                "idempotency_key": "sess_12345678", "based_on_seq": 0,
            },
        )
    assert response.status_code == 201
    assert response.json()["turn"]["speaker"] == "user"
    kwargs = append.call_args.kwargs
    assert kwargs["actor"] == {
        "kind": "session", "user_id": "u1", "user_name": "User", "locale": "ko"
    }
    assert "content" not in response.json()
    assert "carried_over_doc_id" not in response.json()


def test_session_idempotent_replay_is_200_and_domain_error_uses_detail_envelope():
    with patch.object(service, "append_turn", return_value=_result(replayed=True)):
        replay = _client().post(
            "/documents/flowgate.default.0351.0002-CH/conversation/turn",
            json={"body": "hello", "idempotency_key": "sess_12345678"},
        )
    assert replay.status_code == 200
    with patch.object(
        service, "append_turn",
        side_effect=service.ConversationTurnError(409, "idempotency conflict"),
    ):
        conflict = _client().post(
            "/documents/flowgate.default.0351.0002-CH/conversation/turn",
            json={"body": "hello", "idempotency_key": "sess_12345678"},
        )
    assert conflict.status_code == 409
    assert conflict.json() == {"detail": "idempotency conflict"}


def test_old_whole_file_append_route_was_removed_from_documents_router():
    from modules.flow_gate.documents.routers import documents

    paths = [route.path for route in documents.router.routes]
    assert "/documents/{doc_id}/conversation/turn" not in paths