"""0360 T0004 -- chat turn dry-run + corruption check.

NR0003 root cause: chat turns have no dry-run at all, and the "??????" corruption
signature is a submission-environment artifact (Windows console code page), not a
server bug (see group 0114's `_label_is_corrupted`). This wires the existing inbox
dry-run counter (`tokens.dry_run_count`, migration 043/075) and the existing
corruption detector into the chat turn-append endpoint, without inventing a new
schema, a chat-only env var, or a second copy of the corruption threshold.
"""
from __future__ import annotations

import inspect
from unittest.mock import patch

from fastapi import FastAPI
from starlette.testclient import TestClient

from modules.flow_gate.api.v1 import conversation_routes as routes
from modules.flow_gate.services import conversation_turn_service as service
from modules.flow_gate.services import token_service, workflow_decision_service

DOC_ID = "flowgate.default.0360.0002-CH"
TOKEN = {
    "token_id": "tok_20260731_000001",
    "project": "flowgate",
    "group_id": "flowgate.default.0360",
    "doc_ref": DOC_ID,
    "action_scope": "chat",
    "issued_to": "u1",
    "provider_id": "provider-codex",
    "ai_run_id": "run-server-owned",
    "consumed_at": None,
    "dry_run_count": 0,
}
DOC = {
    "doc_id": DOC_ID,
    "project_id": "flowgate",
    "group_id": "flowgate.default.0360",
    "type_code": "CH",
}


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(routes.router)
    return TestClient(app)


def _post(client: TestClient, **overrides):
    body = {"body": "hello", "idempotency_key": TOKEN["token_id"]}
    body.update(overrides)
    return client.post(
        f"/api/v1/conversation/{DOC_ID}/turn",
        headers={"Authorization": "Bearer raw"},
        json=body,
    )


def _append_result(replayed: bool = False) -> dict:
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


# ── dry_run: true never inserts, never consumes; only side effect is the counter ────

def test_dry_run_never_inserts_or_consumes_and_bumps_counter_once(monkeypatch):
    monkeypatch.setattr(service, "_validate_document_for_append", lambda doc_id: DOC)
    insert_calls = []
    consume_calls = []
    increments = []
    monkeypatch.setattr(
        service.turn_store, "insert_turn_with_next_seq", lambda **kw: insert_calls.append(kw)
    )
    monkeypatch.setattr(token_service, "consume", lambda *a, **k: consume_calls.append((a, k)))
    monkeypatch.setattr(
        token_service, "increment_dry_run", lambda token_id: increments.append(token_id)
    )

    with patch.object(routes.token_service, "inspect_for_replay", return_value=dict(TOKEN)), \
         patch.object(routes.db_documents, "get_by_id", return_value=DOC):
        response = _post(_client(), dry_run=True)

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["dry_run"] is True
    assert payload["corrupted"] is False
    assert not insert_calls
    assert not consume_calls
    assert increments == [TOKEN["token_id"]]


def test_dry_run_ignores_whether_the_token_was_already_consumed(monkeypatch):
    """dry_run must validate-only regardless of consumed_at (T0004 item 1)."""
    monkeypatch.setattr(service, "_validate_document_for_append", lambda doc_id: DOC)
    monkeypatch.setattr(token_service, "increment_dry_run", lambda token_id: None)
    consumed_token = dict(TOKEN, consumed_at="2026-01-01T00:00:00+00:00")

    with patch.object(routes.token_service, "inspect_for_replay", return_value=consumed_token), \
         patch.object(routes.db_documents, "get_by_id", return_value=DOC), \
         patch.object(service, "replay_turn") as replay:
        response = _post(_client(), dry_run=True)

    assert response.status_code == 200
    assert response.json()["dry_run"] is True
    replay.assert_not_called()


# ── corruption detection reuses the group-0114 detector verbatim ───────────────────

def test_corrupted_body_is_flagged_without_leaking_the_body(monkeypatch):
    monkeypatch.setattr(service, "_validate_document_for_append", lambda doc_id: DOC)
    monkeypatch.setattr(token_service, "increment_dry_run", lambda token_id: None)
    corrupted_body = "??? ?? ? 0082(??3) ?? ? ?? ??"

    with patch.object(routes.token_service, "inspect_for_replay", return_value=dict(TOKEN)), \
         patch.object(routes.db_documents, "get_by_id", return_value=DOC):
        response = _post(_client(), dry_run=True, body=corrupted_body)

    payload = response.json()
    assert payload["corrupted"] is True
    assert payload["message"]
    assert corrupted_body not in payload["message"]


def test_clean_korean_and_english_bodies_are_not_flagged(monkeypatch):
    monkeypatch.setattr(service, "_validate_document_for_append", lambda doc_id: DOC)
    monkeypatch.setattr(token_service, "increment_dry_run", lambda token_id: None)

    for clean_body in ("정상적인 한글 본문입니다", "a perfectly normal ASCII message"):
        with patch.object(routes.token_service, "inspect_for_replay", return_value=dict(TOKEN)), \
             patch.object(routes.db_documents, "get_by_id", return_value=DOC):
            response = _post(_client(), dry_run=True, body=clean_body)
        assert response.json()["corrupted"] is False


# ── per-token dry-run limit (shared with inbox, no chat-only env var) ──────────────

def test_dry_run_limit_reached_returns_429_without_bumping(monkeypatch):
    monkeypatch.setattr(service, "_validate_document_for_append", lambda doc_id: DOC)
    monkeypatch.setattr(service, "_dry_run_limit", lambda: 2)
    increments = []
    monkeypatch.setattr(
        token_service, "increment_dry_run", lambda token_id: increments.append(token_id)
    )
    token_at_limit = dict(TOKEN, dry_run_count=2)

    with patch.object(routes.token_service, "inspect_for_replay", return_value=token_at_limit), \
         patch.object(routes.db_documents, "get_by_id", return_value=DOC):
        response = _post(_client(), dry_run=True)

    assert response.status_code == 429
    assert not increments


# ── dry_run: false / omitted is byte-for-byte unchanged (no regression) ────────────

def test_dry_run_false_is_unchanged():
    with patch.object(routes.token_service, "inspect_for_replay", return_value=dict(TOKEN)), \
         patch.object(routes.db_documents, "get_by_id", return_value=DOC), \
         patch.object(service, "append_turn", return_value=_append_result()) as append, \
         patch.object(service, "dry_run_append") as dry:
        response = _post(_client(), dry_run=False, body="done")

    assert response.status_code == 201
    dry.assert_not_called()
    append.assert_called_once()


def test_dry_run_field_omitted_is_unchanged():
    with patch.object(routes.token_service, "inspect_for_replay", return_value=dict(TOKEN)), \
         patch.object(routes.db_documents, "get_by_id", return_value=DOC), \
         patch.object(service, "append_turn", return_value=_append_result()) as append, \
         patch.object(service, "dry_run_append") as dry:
        response = _post(_client(), body="done")

    assert response.status_code == 201
    dry.assert_not_called()
    append.assert_called_once()


# ── the corruption threshold is defined in exactly one place ───────────────────────

def test_corruption_threshold_is_defined_once_and_reused_by_chat():
    """0391 T0005 §5-4: chat now goes through _text_is_corrupted (line-based — a long
    AI turn can dilute the whole-body '?' ratio below threshold exactly like the long
    document bodies NR0004 measured), not _label_is_corrupted directly. _text_is_corrupted
    itself still reuses _label_is_corrupted line-by-line inside workflow_decision_service,
    so the threshold constants stay defined exactly once — this assertion just points at
    the new call name."""
    decision_source = inspect.getsource(workflow_decision_service)
    assert decision_source.count("_CORRUPT_MIN_MARKS = 2") == 1
    assert decision_source.count("_CORRUPT_RATIO = 0.5") == 1

    conversation_source = inspect.getsource(service)
    assert "_CORRUPT_MIN_MARKS" not in conversation_source
    assert "_CORRUPT_RATIO" not in conversation_source
    assert "workflow_decision_service._text_is_corrupted" in conversation_source
