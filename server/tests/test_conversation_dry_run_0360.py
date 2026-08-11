"""0360 T0004 -- chat turn dry-run + corruption check.

NR0003 root cause: chat turns have no dry-run at all, and the "??????" corruption
signature is a submission-environment artifact (Windows console code page), not a
server bug (see group 0114's `_label_is_corrupted`). This wires the existing inbox
dry-run counter (`tokens.dry_run_count`, migration 043/075) and the existing
corruption detector into the chat turn-append endpoint, without inventing a new
schema, a chat-only env var, or a second copy of the corruption threshold.
"""
from __future__ import annotations

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

def test_chat_uses_the_same_line_based_corruption_verdict_as_documents():
    """0391 T0005 §5-4: chat goes through the line-based `_text_is_corrupted`, not
    `_label_is_corrupted` directly — a long AI turn dilutes the whole-body '?' ratio below
    the threshold exactly like the long document bodies NR0004 measured (0.404 on the
    reporting document's own body, under the 0.5 cut).

    0394 T0016 항목 4 (NR0003 §6.2-라): 예전에는 두 모듈의 소스를 읽어 상수 정의 횟수를 세고
    'workflow_decision_service._text_is_corrupted' 라는 호출 문자열이 있는지 확인했다.
    그 방식은 정작 문제를 못 잡는다 — 상수를 채팅 쪽에 복사해 놓고 이름만 다르게 두면
    (`_CHAT_RATIO = 0.5`) 셋 다 통과하면서 임계값은 두 벌이 된다. 그래서 판정 그 자체를
    본다: 같은 입력에 대해 채팅 경로의 판정이 문서 경로의 판정과 일치해야 한다.
    """
    long_clean_tail = "\n".join("정상적인 한국어 문장입니다." for _ in range(200))
    cases = {
        # 한 줄만 깨진 긴 본문 — 전체 비율로는 임계 아래, 줄 단위로는 위. NR0004 의 실물 모양.
        "one corrupted line in a long clean body": "?????? ??\n" + long_clean_tail,
        "all corrupted": "?????? ??????",
        "clean korean": long_clean_tail,
        # '?' 로 끝나는 평범한 영문 — 잡으면 안 되는 쪽.
        "ordinary question": "Done?\nAll good?",
        "empty": "",
    }

    for name, text in cases.items():
        document_verdict = workflow_decision_service._text_is_corrupted(text)
        chat_complaint = service._encoding_violation(
            body_raw=text, body_sha256=None, body_chars=None, force_encoding_reason=None,
        )
        chat_verdict = chat_complaint is not None
        assert chat_verdict == document_verdict, (
            f"{name}: 채팅 판정({chat_verdict})이 문서 판정({document_verdict})과 다르다 — "
            "임계값이 두 벌이 됐다는 뜻이다"
        )

    # 그리고 이 사례가 실제로 '줄 단위라야만 잡히는' 것인지 못박는다. 아니라면 위의 일치
    # 확인은 통과하면서도 0391 이 고친 것을 하나도 지키지 못한다.
    diluted = cases["one corrupted line in a long clean body"]
    assert workflow_decision_service._text_is_corrupted(diluted) is True
    assert workflow_decision_service._label_is_corrupted(diluted) is False
