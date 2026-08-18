"""Cursor reads, read-cursor bookkeeping and single-turn SSE (group 0351, T2).

Backed by a real in-memory SQLite carrying the 074 schema, so the paging, the
monotonic cursors and the CHECK that keeps ``last_viewed_seq <= last_read_seq`` are
exercised against the actual constraints rather than against a mock.
"""
from __future__ import annotations

import hashlib
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest

from modules.flow_gate.db import connection
from modules.flow_gate.db import conversation_turns as turns
from modules.flow_gate.services import conversation_events
from modules.flow_gate.services import conversation_query_service as query
from modules.flow_gate.services import conversation_turn_service as append_service

DOC_ID = "flowgate.default.0351.0002-CH"
SESSION_ACTOR = {"kind": "session", "user_id": "u1", "user_name": "sjm", "locale": "ko"}


class _Txn:
    def __init__(self, conn):
        self.conn = conn
        self.cursor = None

    def execute(self, sql, params=None):
        self.cursor = self.conn.execute(sql, params or [])

    def fetchone(self):
        return self.cursor.fetchone() if self.cursor else None

    def fetchall(self):
        return self.cursor.fetchall() if self.cursor else []


class _DB:
    db_type = 1

    def __init__(self, conn):
        self.conn = conn

    def execute(self, sql, params=None):
        cur = self.conn.execute(sql, params or [])
        self.conn.commit()
        return cur

    def fetch_one(self, sql, params=None):
        return self.conn.execute(sql, params or []).fetchone()

    def fetch_all(self, sql, params=None):
        return self.conn.execute(sql, params or []).fetchall()

    @contextmanager
    def begin_transaction(self):
        self.conn.execute("BEGIN")
        try:
            yield _Txn(self.conn)
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise


@pytest.fixture
def store():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("CREATE TABLE documents(doc_id TEXT PRIMARY KEY)")
    sql_dir = Path(__file__).resolve().parents[1] / "sql/migrations/sqlite"
    conn.executescript((sql_dir / "074_conversation_turns.sql").read_text(encoding="utf-8"))
    conn.executescript((sql_dir / "085_conversation_backward_page_audit.sql").read_text(encoding="utf-8"))
    conn.execute("INSERT INTO documents(doc_id) VALUES (?)", [DOC_ID])
    conn.commit()

    class Store(connection.FlowGateStore):
        def __init__(self):
            self._db = _DB(conn)
            self._sq = None

    old = connection.STORE
    connection.STORE = Store()
    yield conn
    connection.STORE = old
    conn.close()


DOC_ROW = {
    "doc_id": DOC_ID,
    "type_code": "CH",
    "title": "대화",
    "status": "draft",
    "group_id": "flowgate.default.0351",
    "project_id": "flowgate",
    "module": "default",
    "triggered_by": "flowgate.default.0351.0001-R",
    "file_path": "",
}


@pytest.fixture
def doc(monkeypatch):
    """A migrated CH document, so reads exercise paging rather than migration."""
    monkeypatch.setattr(
        query.document_service, "get_document", lambda doc_id: dict(DOC_ROW) if doc_id == DOC_ID else None
    )
    turns.ensure_migration_row(DOC_ID)
    connection.get_store()._execute(
        "UPDATE conversation_docs SET migration_state = 'migrated', intro = ? WHERE doc_id = ?",
        ["---\ntitle: 대화\n---", DOC_ID],
    )
    return dict(DOC_ROW)


def _seed(count: int, body: str = "hello", speaker: str = "user", key: str = "user:u1") -> None:
    for seq in range(1, count + 1):
        text = body if isinstance(body, str) else body(seq)
        turns.insert_migrated_turn(
            doc_id=DOC_ID, seq=seq, speaker=speaker, participant_key=key,
            display_name="sjm", locale="ko", body=text,
            body_hash=hashlib.sha256(text.encode()).hexdigest(),
            based_on_seq=seq - 1, idempotency_key=f"seed:{seq}",
            idempotency_hash=hashlib.sha256(f"seed:{seq}".encode()).hexdigest(),
            created_at="2026-07-29T10:00:00+09:00",
        )


# ── 커서 검증 (P0003 시나리오 21 / L0004 §4-2) ────────────────────────────────

def test_after_and_before_are_mutually_exclusive(store, doc):
    with pytest.raises(append_service.ConversationTurnError) as exc:
        query.list_turns(doc_id=DOC_ID, actor=SESSION_ACTOR, after_seq=3, before_seq=9)
    assert exc.value.status_code == 422
    assert exc.value.message == "after_seq and before_seq are mutually exclusive."


@pytest.mark.parametrize(
    "kwargs, message",
    [
        ({"after_seq": -1}, "after_seq must be >= 0."),
        ({"before_seq": 0}, "before_seq must be >= 1."),
        ({"limit": 0}, "limit must be >= 1."),
    ],
)
def test_cursor_domain_violations_are_422(store, doc, kwargs, message):
    with pytest.raises(append_service.ConversationTurnError) as exc:
        query.list_turns(doc_id=DOC_ID, actor=SESSION_ACTOR, **kwargs)
    assert exc.value.status_code == 422
    assert exc.value.message == message


def test_limit_over_the_ceiling_is_trimmed_not_rejected(store, doc):
    _seed(3)
    result = query.list_turns(doc_id=DOC_ID, actor=SESSION_ACTOR, after_seq=0, limit=500)
    # P0003 §0-6: the response reports the limit actually applied.
    assert result["limit"] == query.TURN_LIMIT_MAX == 200
    assert [t["seq"] for t in result["turns"]] == [1, 2, 3]


def test_non_conversation_document_is_400_and_missing_is_404(store, monkeypatch):
    monkeypatch.setattr(
        query.document_service, "get_document",
        lambda doc_id: {**DOC_ROW, "type_code": "D"} if doc_id == DOC_ID else None,
    )
    with pytest.raises(append_service.ConversationTurnError) as not_ch:
        query.list_turns(doc_id=DOC_ID, actor=SESSION_ACTOR)
    assert (not_ch.value.status_code, not_ch.value.message) == (400, "Not a conversation document.")
    with pytest.raises(append_service.ConversationTurnError) as missing:
        query.list_turns(doc_id="flowgate.default.0351.0099-CH", actor=SESSION_ACTOR)
    assert missing.value.status_code == 404


# ── 정방향 / 역방향 페이지 (P0003 시나리오 1·2·7) ─────────────────────────────

def test_forward_page_reports_next_cursor_and_stops_when_exhausted(store, doc):
    _seed(5)
    page = query.list_turns(doc_id=DOC_ID, actor=SESSION_ACTOR, after_seq=0, limit=3)
    assert [t["seq"] for t in page["turns"]] == [1, 2, 3]
    assert page["has_more"] is True
    assert page["truncated_by"] == "limit"
    assert page["next_after_seq"] == 3
    assert page["prev_before_seq"] is None

    tail = query.list_turns(doc_id=DOC_ID, actor=SESSION_ACTOR, after_seq=3, limit=3)
    assert [t["seq"] for t in tail["turns"]] == [4, 5]
    assert tail["has_more"] is False
    assert tail["next_after_seq"] is None
    assert tail["truncated_by"] is None


def test_backward_page_is_seq_ascending_and_carries_prev_cursor(store, doc):
    _seed(6)
    page = query.list_turns(doc_id=DOC_ID, actor=SESSION_ACTOR, before_seq=5, limit=3)
    # Rows are read newest-first but the wire is always ascending (P0003 시나리오 2).
    assert [t["seq"] for t in page["turns"]] == [2, 3, 4]
    assert page["prev_before_seq"] == 2
    assert page["next_after_seq"] is None
    assert page["after_seq"] is None
    assert page["before_seq"] == 5


def test_cursor_at_or_past_head_returns_empty_200_not_an_error(store, doc):
    _seed(2)
    for cursor in (2, 99):
        page = query.list_turns(doc_id=DOC_ID, actor=SESSION_ACTOR, after_seq=cursor)
        assert page["turns"] == []
        assert page["has_more"] is False
        assert page["head_seq"] == 2


def test_missing_cursor_resumes_from_the_remembered_read_position(store, doc):
    _seed(4)
    turns.touch_participant(
        doc_id=DOC_ID, participant_key="user:u1", kind="user",
        display_name="sjm", read_upto=2,
    )
    page = query.list_turns(doc_id=DOC_ID, actor=SESSION_ACTOR)
    assert page["after_seq"] == 2
    assert [t["seq"] for t in page["turns"]] == [3, 4]


# ── 분량 판정 (L0004 §2-9 apply_budget) ───────────────────────────────────────

def test_byte_budget_cuts_before_the_count_limit_and_says_so(store, doc):
    big = "x" * 100_000  # 3 of these blow past the 256 KiB response budget
    for seq in range(1, 4):
        turns.insert_migrated_turn(
            doc_id=DOC_ID, seq=seq, speaker="user", participant_key="user:u1",
            display_name="sjm", locale="ko", body=big,
            body_hash=hashlib.sha256(big.encode()).hexdigest(), based_on_seq=seq - 1,
            idempotency_key=f"big:{seq}",
            idempotency_hash=hashlib.sha256(f"big:{seq}".encode()).hexdigest(),
            created_at="2026-07-29T10:00:00+09:00",
        )
    page = query.list_turns(doc_id=DOC_ID, actor=SESSION_ACTOR, after_seq=0, limit=50)
    assert page["truncated_by"] == "bytes"
    assert [t["seq"] for t in page["turns"]] == [1, 2]
    assert page["next_after_seq"] == 2


def test_a_single_oversized_turn_is_still_delivered_so_paging_can_progress(store, doc):
    huge = "y" * (query.RESPONSE_TURNS_BYTE_MAX + 10_000)
    turns.insert_migrated_turn(
        doc_id=DOC_ID, seq=1, speaker="user", participant_key="user:u1",
        display_name="sjm", locale="ko", body=huge,
        body_hash=hashlib.sha256(huge.encode()).hexdigest(), based_on_seq=0,
        idempotency_key="huge:1",
        idempotency_hash=hashlib.sha256(b"huge:1").hexdigest(),
        created_at="2026-07-29T10:00:00+09:00",
    )
    page = query.list_turns(doc_id=DOC_ID, actor=SESSION_ACTOR, after_seq=0)
    # Returning zero rows here would make the client re-request the same cursor forever.
    assert [t["seq"] for t in page["turns"]] == [1]


def test_budget_disabled_when_the_byte_ceiling_is_misconfigured_to_zero(store, doc, monkeypatch):
    monkeypatch.setattr(query, "RESPONSE_TURNS_BYTE_MAX", 0)
    _seed(3, body="z" * 5000)
    page = query.list_turns(doc_id=DOC_ID, actor=SESSION_ACTOR, after_seq=0, limit=50)
    assert [t["seq"] for t in page["turns"]] == [1, 2, 3]
    assert page["truncated_by"] is None


# ── head / participants / me (P0003 §0-3·0-4, L0004 §2-10) ───────────────────

def test_head_is_only_built_on_request_and_carries_the_background(store, doc):
    _seed(5)
    bare = query.list_turns(doc_id=DOC_ID, actor=SESSION_ACTOR, after_seq=0)
    assert bare["head"] is None
    assert bare["participants"] == []
    assert bare["me"] is None

    full = query.list_turns(doc_id=DOC_ID, actor=SESSION_ACTOR, after_seq=0, include_head=True)
    head = full["head"]
    assert head["doc_id"] == DOC_ID
    assert head["type"] == "CH"
    assert head["title"] == "대화"
    assert head["intro"] == "---\ntitle: 대화\n---"
    assert head["total_turns"] == 5
    assert head["head_seq"] == 5
    assert [t["seq"] for t in head["opening_turns"]] == [1, 2, 3]  # OPENING_TURNS_MAX
    assert full["me"]["participant_key"] == "user:u1"


def test_head_marks_a_carried_over_predecessor_only_for_a_ch_origin(store, doc, monkeypatch):
    predecessor = "flowgate.default.0351.0001-CH"
    monkeypatch.setattr(
        query.document_service, "get_document",
        lambda _doc_id: {**DOC_ROW, "triggered_by": predecessor},
    )
    head = query.build_head({**DOC_ROW, "triggered_by": predecessor})
    assert head["carried_over_from"] == predecessor
    # An R-triggered CH is an ordinary conversation, not a continuation.
    assert query.build_head(dict(DOC_ROW))["carried_over_from"] is None


def test_participants_list_reflects_every_speaker_in_the_conversation(store, doc):
    _seed(2)
    turns.touch_participant(
        doc_id=DOC_ID, participant_key="provider:cx_opus", kind="ai",
        display_name="Claude Opus 5", written_seq=2,
    )
    page = query.list_turns(doc_id=DOC_ID, actor=SESSION_ACTOR, after_seq=0, include_head=True)
    keys = {p["participant_key"] for p in page["participants"]}
    assert "provider:cx_opus" in keys
    # last_viewed_seq is an internal column and must not leak onto the wire (L0004 §2-8).
    assert all("last_viewed_seq" not in p for p in page["participants"])


def test_unknown_participant_reads_from_zero_without_being_recorded(store, doc):
    _seed(2)
    page = query.list_turns(doc_id=DOC_ID, actor=SESSION_ACTOR, after_seq=0, include_head=True)
    assert page["me"]["last_read_seq"] == 0
    assert page["me"]["first_seen_seq"] == 0
    # Merely looking is not participation (L0004 §5).
    assert turns.get_participant(DOC_ID, "user:u1") is None


# ── 읽음 커서 (L0004 §2-8, P0003 시나리오 5·9) ────────────────────────────────

def test_worker_read_advances_delivered_only_to_the_last_turn_actually_sent(store, doc):
    _seed(5)
    token = {"token_id": "tok_1", "provider_id": "cx_opus", "project": "flowgate"}
    with patch.object(append_service, "_provider_row", return_value={"name": "Claude Opus 5"}):
        page = query.list_turns(
            doc_id=DOC_ID, actor={"kind": "worker", "token": token}, after_seq=0, limit=2,
        )
    assert [t["seq"] for t in page["turns"]] == [1, 2]
    row = turns.get_participant(DOC_ID, "provider:cx_opus")
    # 3..5 were truncated away; advancing past them would skip them forever.
    assert row["last_read_seq"] == 2
    # delivered is not "seen by a human" — the viewed boundary stays put.
    assert row["last_viewed_seq"] == 0


def test_scrolling_up_is_audited_without_advancing_any_cursor(store, doc):
    _seed(6)
    token = {"token_id": "tok_1", "provider_id": "cx_opus", "project": "flowgate"}
    with patch.object(append_service, "_provider_row", return_value={"name": "Opus"}):
        query.list_turns(
            doc_id=DOC_ID, actor={"kind": "worker", "token": token}, before_seq=5, limit=3,
        )
    assert turns.get_participant(DOC_ID, "provider:cx_opus") is None
    audit = connection.get_store()._fetch_one(
        "SELECT * FROM conversation_backward_page_audit WHERE doc_id = ?", [DOC_ID]
    )
    assert audit["participant_key"] == "provider:cx_opus"
    assert audit["actor_kind"] == "worker"
    assert audit["before_seq"] == 5
    assert audit["returned_count"] == 3


def test_viewed_advances_both_cursors_and_never_moves_backwards(store, doc):
    _seed(5)
    first = query.record_read(doc_id=DOC_ID, actor=SESSION_ACTOR, last_read_seq=4, reason="viewed")
    assert first["me"]["last_read_seq"] == 4
    row = turns.get_participant(DOC_ID, "user:u1")
    assert (row["last_read_seq"], row["last_viewed_seq"]) == (4, 4)

    late = query.record_read(doc_id=DOC_ID, actor=SESSION_ACTOR, last_read_seq=1, reason="viewed")
    assert late["me"]["last_read_seq"] == 4  # silently ignored, not an error


def test_a_read_claim_beyond_the_head_is_clamped(store, doc):
    _seed(3)
    result = query.record_read(doc_id=DOC_ID, actor=SESSION_ACTOR, last_read_seq=99, reason="viewed")
    assert result["me"]["last_read_seq"] == 3
    assert result["head_seq"] == 3


def test_read_rejects_an_unknown_reason_and_a_negative_cursor(store, doc):
    for kwargs, message in (
        ({"last_read_seq": 1, "reason": "skimmed"}, "unknown reason."),
        ({"last_read_seq": -1}, "last_read_seq must be >= 0."),
    ):
        with pytest.raises(append_service.ConversationTurnError) as exc:
            query.record_read(doc_id=DOC_ID, actor=SESSION_ACTOR, **kwargs)
        assert exc.value.status_code == 422
        assert exc.value.message == message


def test_delivered_then_a_late_viewed_keeps_viewed_below_read(store, doc):
    _seed(5)
    query.record_read(doc_id=DOC_ID, actor=SESSION_ACTOR, last_read_seq=5, reason="delivered")
    query.record_read(doc_id=DOC_ID, actor=SESSION_ACTOR, last_read_seq=3, reason="viewed")
    row = turns.get_participant(DOC_ID, "user:u1")
    # The 074 CHECK (last_viewed_seq <= last_read_seq) must hold through any ordering.
    assert row["last_read_seq"] == 5
    assert row["last_viewed_seq"] == 3


# ── 실시간 단건 전달 (P0003 시나리오 6, L0004 §2-11) ──────────────────────────

def test_appended_turn_is_broadcast_as_one_turn_not_a_document_refresh():
    result = {
        "doc_id": DOC_ID, "head_seq": 14,
        "turn": {"seq": 14, "speaker": "ai", "body": "…"},
        "me": {"participant_key": "provider:cx_opus", "last_written_seq": 14},
    }
    with patch(
        "modules.flow_gate.api.v1.events.publisher.broadcast_event_threadsafe", return_value=1
    ) as broadcast:
        conversation_events.broadcast_turn_appended(dict(DOC_ROW), result)
    event = broadcast.call_args.args[0]
    assert event.event_type.value == "conversation_turn_appended"
    assert event.audience == "*"
    assert event.doc_id == DOC_ID
    assert event.project == "flowgate"
    assert event.group_id == "flowgate.default.0351"
    assert event.payload["turn"]["seq"] == 14
    assert event.payload["head_seq"] == 14
    assert event.payload["participant"]["participant_key"] == "provider:cx_opus"
    # The whole conversation body must never ride on the event (D0002 §6).
    assert "content" not in event.payload


def test_a_broadcast_failure_never_propagates_to_the_committed_turn():
    with patch(
        "modules.flow_gate.api.v1.events.publisher.broadcast_event_threadsafe",
        side_effect=RuntimeError("no loop"),
    ):
        assert conversation_events.broadcast_turn_appended(
            dict(DOC_ROW), {"doc_id": DOC_ID, "head_seq": 1, "turn": {"seq": 1}, "me": None}
        ) == 0


def test_document_explorer_refresh_remains_a_separate_event_type():
    from modules.flow_gate.api.v1.events.event_types import EventType

    assert EventType.CONVERSATION_TURN_APPENDED.value == "conversation_turn_appended"
    assert EventType.DOCUMENT_EXPLORER_REFRESH.value == "document_explorer_refresh"


# ── 라우트 등록 (P0003 §0-1 경로표) ───────────────────────────────────────────

def test_session_and_worker_query_paths_are_registered_exactly_once():
    from modules.flow_gate.api.v1 import conversation_routes
    from modules.flow_gate.documents.routers import conversation_turns as session_routes

    session = [r.path for r in session_routes.router.routes]
    worker = [r.path for r in conversation_routes.router.routes]
    assert session.count("/documents/{doc_id}/conversation/turns") == 1
    assert session.count("/documents/{doc_id}/conversation/read") == 1
    assert session.count("/documents/{doc_id}/conversation/turn") == 1
    assert worker.count("/api/v1/conversation/{doc_id}/turns") == 1
    assert worker.count("/api/v1/conversation/{doc_id}/turn") == 1


# ── HTTP 수용 (P0003 §0-5 오류 봉투) ──────────────────────────────────────────

def _session_client():
    from fastapi import FastAPI
    from starlette.testclient import TestClient

    from modules.flow_gate.auth.middleware import get_current_user
    from modules.flow_gate.documents.routers import conversation_turns as routes

    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[get_current_user] = lambda: {"user_id": "u1", "username": "sjm"}
    return TestClient(app)


def _worker_client():
    from fastapi import FastAPI
    from starlette.testclient import TestClient

    from modules.flow_gate.api.v1 import conversation_routes

    app = FastAPI()
    app.include_router(conversation_routes.router)
    return TestClient(app)


def test_session_query_returns_the_page_and_keeps_the_detail_error_envelope():
    page = {"ok": True, "doc_id": DOC_ID, "turns": [], "head_seq": 0, "me": None}
    with patch.object(query, "list_turns", return_value=page) as listed:
        response = _session_client().get(
            f"/documents/{DOC_ID}/conversation/turns?after_seq=3&limit=10&include_head=1"
        )
    assert response.status_code == 200
    assert response.json() == page
    kwargs = listed.call_args.kwargs
    assert kwargs["after_seq"] == 3
    assert kwargs["limit"] == 10
    assert kwargs["include_head"] is True
    # Identity comes from the session, never from the query string.
    assert kwargs["actor"]["kind"] == "session"
    assert kwargs["actor"]["user_id"] == "u1"

    with patch.object(
        query, "list_turns",
        side_effect=append_service.ConversationTurnError(422, "after_seq must be >= 0."),
    ):
        bad = _session_client().get(f"/documents/{DOC_ID}/conversation/turns?after_seq=-1")
    assert bad.status_code == 422
    assert bad.json() == {"detail": "after_seq must be >= 0."}


def test_session_read_route_forwards_the_reported_position():
    with patch.object(query, "record_read", return_value={"ok": True}) as recorded:
        response = _session_client().post(
            f"/documents/{DOC_ID}/conversation/read",
            json={"last_read_seq": 13, "reason": "viewed"},
        )
    assert response.status_code == 200
    assert recorded.call_args.kwargs["last_read_seq"] == 13
    assert recorded.call_args.kwargs["reason"] == "viewed"


def test_worker_query_uses_the_worker_error_envelope_and_never_consumes_the_token():
    token = {
        "token_id": "tok_1", "action_scope": "chat", "doc_ref": DOC_ID,
        "project": "flowgate", "group_id": "flowgate.default.0351",
        "provider_id": "cx_opus", "consumed_at": None,
    }
    page = {"ok": True, "doc_id": DOC_ID, "turns": [], "head_seq": 0}
    from modules.flow_gate.api.v1 import conversation_routes

    with patch.object(conversation_routes.token_service, "inspect_for_replay", return_value=token), \
         patch.object(conversation_routes.db_documents, "get_by_id",
                      return_value={"project_id": "flowgate", "group_id": "flowgate.default.0351"}), \
         patch.object(conversation_routes.token_service, "consume") as consume, \
         patch.object(query, "list_turns", return_value=page):
        response = _worker_client().get(
            f"/api/v1/conversation/{DOC_ID}/turns?after_seq=13&include_head=1",
            headers={"Authorization": "Bearer tok_v1_x"},
        )
    assert response.status_code == 200
    assert response.json() == page
    # Paging through a long conversation must stay free (P0003 §0-1).
    consume.assert_not_called()


def test_worker_query_rejects_a_token_bound_to_another_document():
    from modules.flow_gate.api.v1 import conversation_routes

    token = {
        "token_id": "tok_1", "action_scope": "chat",
        "doc_ref": "flowgate.default.0351.0009-CH",
        "project": "flowgate", "group_id": "flowgate.default.0351",
    }
    with patch.object(conversation_routes.token_service, "inspect_for_replay", return_value=token):
        response = _worker_client().get(
            f"/api/v1/conversation/{DOC_ID}/turns",
            headers={"Authorization": "Bearer tok_v1_x"},
        )
    assert response.status_code == 403
    body = response.json()
    # Worker routes keep their own envelope; mixing in {"detail": ...} would break every
    # existing worker's error handling (P0003 §0-5).
    assert body["ok"] is False
    assert body["http_status"] == 403
    assert "error_message" in body and "help_url" in body


def test_worker_query_without_a_bearer_header_is_401():
    response = _worker_client().get(f"/api/v1/conversation/{DOC_ID}/turns")
    assert response.status_code == 401
    assert response.json()["ok"] is False


def test_worker_read_route_is_async_and_offloads_blocking_work():
    """Reading hits the DB synchronously, so it must not run on the event loop.

    0394 T0016 항목 4 (NR0003 §5.3): 예전에는 이 함수의 소스에 'anyio.to_thread.run_sync'
    라는 글자가 있는지만 봤다. 주석에 있어도 통과하고, 오프로드를 실제로 걷어내도 다른 줄에
    글자가 남아 있으면 통과한다. 같은 규칙 — "async 라우트 핸들러는 동기 DB/파일 작업에
    닿지 않는다" — 을 소스 트리 전체에 강제하는 가드가 이미 있으므로
    (test_event_loop_blocking_0279), 그 판정기를 그대로 불러 이 파일에 적용한다.
    규칙을 두 벌 쓰지 않으면서, 이 라우터가 그 규칙 안에 있다는 사실도 함께 고정된다.
    """
    from pathlib import Path

    from test_event_loop_blocking_0279 import find_blocking_calls

    routes_path = (
        Path(__file__).resolve().parents[1]
        / "modules" / "flow_gate" / "api" / "v1" / "conversation_routes.py"
    )
    findings = find_blocking_calls(
        routes_path.read_text(encoding="utf-8"), filename="conversation_routes.py"
    )
    assert findings == [], "\n  ".join(["conversation_routes blocks the event loop:", *findings])

    # ...and the detector is actually looking at this route (a renamed handler would
    # otherwise leave the assertion above trivially true).
    assert "list_worker_conversation_turns" in routes_path.read_text(encoding="utf-8")
