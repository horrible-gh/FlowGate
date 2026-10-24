"""Worker conversation adapter trust-boundary tests for group 0351."""
from __future__ import annotations

from unittest.mock import patch

from fastapi import FastAPI
from starlette.testclient import TestClient

from modules.flow_gate.api.v1 import ai_invoke_routes
from modules.flow_gate.api.v1 import conversation_routes as routes
from modules.flow_gate.services import ai_invoke_service
from modules.flow_gate.services import conversation_turn_service as service

DOC_ID = "flowgate.default.0351.0002-CH"
TOKEN = {
    "token_id": "tok_20260729_000001",
    "project": "flowgate",
    "group_id": "flowgate.default.0351",
    "doc_ref": DOC_ID,
    "action_scope": "chat",
    "issued_to": "u1",
    "provider_id": "provider-codex",
    "ai_run_id": "run-server-owned",
    "consumed_at": None,
}
DOC = {
    "doc_id": DOC_ID,
    "project_id": "flowgate",
    "group_id": "flowgate.default.0351",
    "type_code": "CH",
}


def _result(replayed: bool = False) -> dict:
    return {
        "ok": True, "doc_id": DOC_ID, "replayed": replayed, "head_seq": 1,
        "turn": {
            "seq": 1, "speaker": "ai", "participant_key": "provider:provider-codex",
            "display_name": "Codex", "locale": None, "body": "done",
            "based_on_seq": 0, "stale_since_seq": None,
            "source_run_id": "run-server-owned", "created_at": "t",
        },
        "me": {
            "participant_key": "provider:provider-codex", "kind": "ai",
            "display_name": "Codex", "first_seen_seq": 1, "last_read_seq": 0,
            "last_written_seq": 1, "last_seen_at": "t",
        },
    }


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(routes.router)
    return TestClient(app)


def _post(client: TestClient, **overrides):
    body = {
        "body": "done", "idempotency_key": TOKEN["token_id"], "based_on_seq": 0,
        # These unmodelled claims are intentionally ignored by pydantic/the adapter.
        "speaker": "user", "provider_id": "forged", "source_run_id": "forged",
    }
    body.update(overrides)
    return client.post(
        f"/api/v1/conversation/{DOC_ID}/turn",
        headers={"Authorization": "Bearer raw"},
        json=body,
    )


def test_unconsumed_chat_token_calls_the_shared_append_service():
    with patch.object(routes.token_service, "inspect_for_replay", return_value=dict(TOKEN)), \
         patch.object(routes.db_documents, "get_by_id", return_value=DOC), \
         patch.object(service, "append_turn", return_value=_result()) as append:
        response = _post(_client())
    assert response.status_code == 201
    assert response.json()["message"].startswith("Turn 1 appended")
    actor = append.call_args.kwargs["actor"]
    assert actor == {"kind": "worker", "token": TOKEN}
    assert append.call_args.kwargs.get("source_run_id") is None


def test_consumed_token_replays_before_single_use_rejection():
    consumed = dict(TOKEN, consumed_at="2026-01-01T00:00:00+00:00")
    with patch.object(routes.token_service, "inspect_for_replay", return_value=consumed), \
         patch.object(routes.db_documents, "get_by_id", return_value=DOC), \
         patch.object(service, "replay_turn", return_value=_result(replayed=True)):
        response = _post(_client())
    assert response.status_code == 200
    assert response.json()["replayed"] is True

    with patch.object(routes.token_service, "inspect_for_replay", return_value=consumed), \
         patch.object(routes.db_documents, "get_by_id", return_value=DOC), \
         patch.object(service, "replay_turn", return_value=None):
        rejected = _post(_client())
    assert rejected.status_code == 401
    assert rejected.json()["ok"] is False


def test_scope_binding_and_server_token_idempotency_are_enforced():
    wrong = dict(TOKEN, action_scope="edit")
    with patch.object(routes.token_service, "inspect_for_replay", return_value=wrong):
        response = _post(_client())
    assert response.status_code == 403
    assert set(response.json()) == {"ok", "http_status", "error_message", "help_url"}

    with patch.object(routes.token_service, "inspect_for_replay", return_value=dict(TOKEN)), \
         patch.object(routes.db_documents, "get_by_id", return_value=DOC):
        mismatch = _post(_client(), idempotency_key="sess_not_the_token")
    assert mismatch.status_code == 422
    assert "token id" in mismatch.json()["error_message"]

def test_ai_invoke_chat_token_contract_passes_worker_read_and_append_routes():
    """The in-app scope mapping is accepted by both real worker HTTP adapters."""
    token = dict(TOKEN, action_scope=ai_invoke_routes._TOKEN_SCOPE["chat"])
    page = {"ok": True, "doc_id": DOC_ID, "turns": [], "head_seq": 0}
    client = _client()
    with patch.object(routes.token_service, "inspect_for_replay", return_value=token), \
         patch.object(routes.db_documents, "get_by_id", return_value=DOC), \
         patch.object(routes.conversation_query_service, "list_turns", return_value=page), \
         patch.object(service, "append_turn", return_value=_result()):
        read = client.get(
            f"/api/v1/conversation/{DOC_ID}/turns?after_seq=0&include_head=1",
            headers={"Authorization": "Bearer raw"},
        )
        written = _post(client)

    assert ai_invoke_routes._TOKEN_SCOPE["chat"] == "chat"
    assert read.status_code == 200
    assert written.status_code == 201

def test_chat_completion_oracle_tracks_the_append_only_turn_head(monkeypatch):
    heads = iter((4, 5))
    monkeypatch.setattr(
        ai_invoke_service.db_conversation_turns,
        "current_head_seq",
        lambda doc_id: next(heads),
    )
    oracle = ai_invoke_service._scope_oracle("chat", None, DOC_ID)

    assert oracle is not None
    assert oracle() is True
