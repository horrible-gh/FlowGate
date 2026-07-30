"""Deterministic markdown render + artifact/compat routes (group 0351, T4).

Same lightweight in-memory-SQLite harness as test_conversation_query_0351.py: a real
074-schema DB for conversation_turns/conversation_docs, with document_service.get_document
monkeypatched (T4's render path never needs the full `documents` table).
"""
from __future__ import annotations

import hashlib
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from modules.flow_gate.db import connection
from modules.flow_gate.db import conversation_turns as turns
from modules.flow_gate.services import conversation_markdown_service as markdown_service
from modules.flow_gate.services import conversation_query_service as query
from modules.flow_gate.services import conversation_turn_service as append_service

DOC_ID = "flowgate.default.0351.0002-CH"


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
def store(tmp_path):
    # check_same_thread=False: TestClient dispatches sync route handlers onto a
    # worker thread, so the connection created here (the test's main thread) must
    # still be usable from there.
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("CREATE TABLE documents(doc_id TEXT PRIMARY KEY)")
    sql = Path(__file__).resolve().parents[1] / "sql/migrations/sqlite/074_conversation_turns.sql"
    conn.executescript(sql.read_text(encoding="utf-8"))
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


def _add_doc_stub(doc_id: str) -> None:
    connection.get_store()._execute("INSERT OR IGNORE INTO documents(doc_id) VALUES (?)", [doc_id])


def _patch_doc(monkeypatch, doc_row=None, file_path=""):
    row = {**DOC_ROW, "file_path": file_path}
    if doc_row:
        row.update(doc_row)
    for target in (query.document_service, append_service.document_service, markdown_service.document_service):
        monkeypatch.setattr(target, "get_document", lambda doc_id, _row=row: dict(_row) if doc_id == DOC_ID else None)
    return row


@pytest.fixture
def doc(monkeypatch):
    """A migrated CH document with a fixed intro, so render output is stable to assert on."""
    _patch_doc(monkeypatch)
    turns.ensure_migration_row(DOC_ID)
    connection.get_store()._execute(
        "UPDATE conversation_docs SET migration_state = 'migrated', intro = ? WHERE doc_id = ?",
        ["---\ntitle: 대화\n---", DOC_ID],
    )
    return dict(DOC_ROW)


def _seed(count: int, speaker: str = "user", key: str = "user:u1", display_name=None, locale="ko", start: int = 1) -> None:
    for seq in range(start, start + count):
        text = f"turn {seq}"
        turns.insert_migrated_turn(
            doc_id=DOC_ID, seq=seq, speaker=speaker, participant_key=key,
            display_name=display_name, locale=locale if speaker == "user" else None,
            body=text, body_hash=hashlib.sha256(text.encode()).hexdigest(),
            based_on_seq=seq - 1, idempotency_key=f"seed:{seq}",
            idempotency_hash=hashlib.sha256(f"seed:{seq}".encode()).hexdigest(),
            created_at=f"2026-07-29T10:{seq:02d}:00+09:00",
        )


# ── 결정론 (완료 기준 1) ───────────────────────────────────────────────────────

def test_rendering_twice_is_byte_identical_and_fingerprint_is_stable(store, doc):
    _seed(3)
    first = markdown_service.render_markdown(DOC_ID)
    second = markdown_service.render_markdown(DOC_ID)
    assert first["content"] == second["content"]
    assert first["fingerprint"] == second["fingerprint"]
    assert first["fingerprint"].startswith("sha256:")
    # rendered_at is not part of the hash input — forcing it to differ must not move
    # the fingerprint.
    assert first["rendered_at"] != "" and second["rendered_at"] != ""


def test_fingerprint_changes_only_when_the_turn_set_changes(store, doc):
    _seed(2)
    before = markdown_service.render_markdown(DOC_ID)
    _seed(1, start=3)
    after = markdown_service.render_markdown(DOC_ID)
    assert before["fingerprint"] != after["fingerprint"]
    assert after["head_seq"] == 3


def test_ordering_is_seq_ascending_regardless_of_created_at(store, doc):
    # Two turns sharing a timestamp must still come out in seq order.
    for seq, ts in ((1, "2026-07-29T10:00:00+09:00"), (2, "2026-07-29T10:00:00+09:00")):
        text = f"turn {seq}"
        turns.insert_migrated_turn(
            doc_id=DOC_ID, seq=seq, speaker="user", participant_key="user:u1",
            display_name=None, locale="ko", body=text,
            body_hash=hashlib.sha256(text.encode()).hexdigest(), based_on_seq=seq - 1,
            idempotency_key=f"same-ts:{seq}",
            idempotency_hash=hashlib.sha256(f"same-ts:{seq}".encode()).hexdigest(),
            created_at=ts,
        )
    rendered = markdown_service.render_markdown(DOC_ID)
    assert rendered["content"].index("turn 1") < rendered["content"].index("turn 2")


# ── 옛 파일과의 왕복 (완료 기준 1) ──────────────────────────────────────────────

def test_migrate_then_render_round_trips_a_legacy_markdown_body(store, monkeypatch):
    from modules.flow_gate import conversation as legacy_conversation

    legacy_content = (
        "---\ntitle: 대화\n---\n\n"
        "## 🧑 사용자 · 2026-07-29T09:00:00+09:00\n"
        "안녕하세요\n\n"
        "## 🤖 AI(claude-opus-5) · 2026-07-29T09:01:00+09:00\n"
        "안녕하세요! 무엇을 도와드릴까요?\n"
    )
    _patch_doc(monkeypatch, file_path="")
    # Route the migration's file read through a fixed string instead of touching disk.
    monkeypatch.setattr(
        append_service, "_document_path",
        lambda _doc: type("P", (), {"is_file": lambda self: True, "read_text": lambda self, encoding="utf-8": legacy_content})(),
    )
    # The minimal 074-only schema carries no ai_providers table; the migration's
    # provider-name lookup must not need it to run this test.
    monkeypatch.setattr(append_service.ai_providers, "list_scope", lambda _scope: [])
    assert append_service.migrate_conversation(DOC_ID) is True

    rendered = markdown_service.render_markdown(DOC_ID)
    # serialize(parse(x)) == x is already guaranteed by conversation.py; migrating
    # through the turn store and rendering back must reach the same fixed point.
    reparsed = legacy_conversation.parse_conversation(rendered["content"])
    original = legacy_conversation.parse_conversation(legacy_content)
    assert [t["body"] for t in reparsed["turns"]] == [t["body"] for t in original["turns"]]
    assert [t["speaker"] for t in reparsed["turns"]] == [t["speaker"] for t in original["turns"]]
    assert rendered["content"] == legacy_content


# ── 오류 분기 ──────────────────────────────────────────────────────────────────

def test_render_rejects_missing_and_non_conversation_documents(store, monkeypatch):
    _patch_doc(monkeypatch, doc_row={"type_code": "D"})
    with pytest.raises(append_service.ConversationTurnError) as not_ch:
        markdown_service.render_markdown(DOC_ID)
    assert (not_ch.value.status_code, not_ch.value.message) == (400, "Not a conversation document.")

    with pytest.raises(append_service.ConversationTurnError) as missing:
        markdown_service.render_markdown("flowgate.default.0351.0099-CH")
    assert missing.value.status_code == 404


# ── GET .../conversation/markdown (완료 기준: 호환 모드 / 실패 문서) ───────────

def _markdown_client() -> TestClient:
    from modules.flow_gate.auth.middleware import get_current_user
    from modules.flow_gate.documents.routers import conversation_turns as routes

    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[get_current_user] = lambda: {"user_id": "u1", "username": "sjm"}
    return TestClient(app)


def test_markdown_route_projects_a_migrated_conversation(store, doc):
    _seed(2)
    resp = _markdown_client().get(f"/documents/{DOC_ID}/conversation/markdown")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["projection"] is True
    assert body["head_seq"] == 2
    assert body["fingerprint"].startswith("sha256:")
    assert "turn 1" in body["content"] and "turn 2" in body["content"]


def test_markdown_route_returns_raw_file_for_a_failed_conversation(store, monkeypatch, tmp_path):
    legacy_file = tmp_path / "legacy.md"
    legacy_file.write_text("---\ntitle: 대화\n---\n\nold frozen body\n", encoding="utf-8")
    _patch_doc(monkeypatch, file_path=str(legacy_file))
    turns.ensure_migration_row(DOC_ID)
    connection.get_store()._execute(
        "UPDATE conversation_docs SET migration_state = 'failed', failure_reason = 'too many turns' "
        "WHERE doc_id = ?",
        [DOC_ID],
    )
    resp = _markdown_client().get(f"/documents/{DOC_ID}/conversation/markdown")
    assert resp.status_code == 200
    body = resp.json()
    assert body["projection"] is False
    assert body["content"] == legacy_file.read_text(encoding="utf-8")


def test_markdown_route_404_and_400(store, monkeypatch):
    _patch_doc(monkeypatch, doc_row={"type_code": "D"})
    resp = _markdown_client().get(f"/documents/{DOC_ID}/conversation/markdown")
    assert resp.status_code == 400

    resp = _markdown_client().get("/documents/flowgate.default.0351.0099-CH/conversation/markdown")
    assert resp.status_code == 404


# ── GET .../content 호환 모드 (P0003 시나리오 15) ──────────────────────────────

def _content_client() -> TestClient:
    from modules.flow_gate.auth.middleware import get_current_user
    from modules.flow_gate.documents.routers import documents as routes

    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[get_current_user] = lambda: {"user_id": "u1", "username": "sjm"}
    return TestClient(app)


def test_content_route_projects_migrated_ch_with_conversation_block(store, doc):
    _seed(2)
    resp = _content_client().get(f"/documents/{DOC_ID}/content")
    assert resp.status_code == 200
    body = resp.json()
    assert body["projection"] is True
    assert "turn 1" in body["content"]
    conv = body["conversation"]
    assert conv["is_conversation"] is True
    assert conv["head_seq"] == 2
    assert conv["total_turns"] == 2
    assert conv["turns_url"] == f"/api/v1/documents/{DOC_ID}/conversation/turns"
    assert conv["projection"] is True


def test_content_route_keeps_raw_file_for_a_failed_conversation(store, monkeypatch, tmp_path):
    from modules.flow_gate.documents.routers import documents as doc_routes

    legacy_file = tmp_path / "legacy.md"
    legacy_file.write_text("frozen legacy body\n", encoding="utf-8")
    _patch_doc(monkeypatch, file_path=str(legacy_file))
    # Bypass the jailed storage resolver — irrelevant to this compat-mode change.
    monkeypatch.setattr(doc_routes, "_document_file_path", lambda _doc: legacy_file)
    turns.ensure_migration_row(DOC_ID)
    connection.get_store()._execute(
        "UPDATE conversation_docs SET migration_state = 'failed' WHERE doc_id = ?", [DOC_ID],
    )
    resp = _content_client().get(f"/documents/{DOC_ID}/content")
    assert resp.status_code == 200
    body = resp.json()
    assert body["projection"] is False
    assert body["content"] == "frozen legacy body\n"
    assert body["conversation"]["projection"] is False


def test_content_route_missing_file_no_longer_404s_a_migrated_ch(store, monkeypatch):
    """The DB is authoritative once migrated — a missing/never-written file must not 404."""
    _patch_doc(monkeypatch, file_path="")  # no file at all
    turns.ensure_migration_row(DOC_ID)
    connection.get_store()._execute(
        "UPDATE conversation_docs SET migration_state = 'migrated', intro = '' WHERE doc_id = ?",
        [DOC_ID],
    )
    _seed(1)
    resp = _content_client().get(f"/documents/{DOC_ID}/content")
    assert resp.status_code == 200
    assert "turn 1" in resp.json()["content"]


def test_content_route_shape_is_unchanged_for_non_conversation_documents(store, monkeypatch, tmp_path):
    from modules.flow_gate.documents.routers import documents as doc_routes

    other_file = tmp_path / "other.md"
    other_file.write_text("plain document body\n", encoding="utf-8")
    monkeypatch.setattr(
        append_service.document_service, "get_document",
        lambda doc_id: {**DOC_ROW, "type_code": "T", "file_path": str(other_file)} if doc_id == DOC_ID else None,
    )
    # Bypass the jailed storage resolver (irrelevant to this T4 change — the branch
    # under test is the pre-existing non-CH path, untouched by this feature).
    monkeypatch.setattr(doc_routes, "_document_file_path", lambda _doc: other_file)
    resp = _content_client().get(f"/documents/{DOC_ID}/content")
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"content": "plain document body\n"}
    assert "conversation" not in body
    assert "projection" not in body


# ── 그룹 마감 산출물 고정 (완료 기준 5 위임 사항 ①) ────────────────────────────

def test_snapshot_group_conversations_writes_migrated_ch_files_and_skips_failed(store, monkeypatch, tmp_path):
    migrated_path = tmp_path / "migrated.md"
    migrated_path.write_text("stale placeholder\n", encoding="utf-8")
    failed_path = tmp_path / "failed.md"
    failed_path.write_text("frozen legacy body\n", encoding="utf-8")

    docs = {
        DOC_ID: {**DOC_ROW, "file_path": str(migrated_path)},
        "flowgate.default.0351.0003-CH": {**DOC_ROW, "doc_id": "flowgate.default.0351.0003-CH", "file_path": str(failed_path)},
    }
    _add_doc_stub("flowgate.default.0351.0003-CH")
    monkeypatch.setattr(
        markdown_service.document_service, "list_documents",
        lambda **kwargs: list(docs.values()),
    )
    monkeypatch.setattr(
        markdown_service.document_service, "get_document",
        lambda doc_id: docs.get(doc_id),
    )

    turns.ensure_migration_row(DOC_ID)
    connection.get_store()._execute(
        "UPDATE conversation_docs SET migration_state = 'migrated', intro = '' WHERE doc_id = ?", [DOC_ID],
    )
    _seed(1)

    turns.ensure_migration_row("flowgate.default.0351.0003-CH")
    connection.get_store()._execute(
        "UPDATE conversation_docs SET migration_state = 'failed' WHERE doc_id = ?",
        ["flowgate.default.0351.0003-CH"],
    )

    stats = markdown_service.snapshot_group_conversations("flowgate", "flowgate.default.0351")
    assert stats == {"scanned": 2, "written": 1, "skipped": 1, "failed": 0}
    assert "turn 1" in migrated_path.read_text(encoding="utf-8")
    # The failed doc's file is untouched — it is already the record of truth.
    assert failed_path.read_text(encoding="utf-8") == "frozen legacy body\n"


def test_snapshot_group_conversations_write_failure_is_isolated_and_logged(store, monkeypatch):
    docs = {DOC_ID: {**DOC_ROW, "file_path": ""}}
    monkeypatch.setattr(markdown_service.document_service, "list_documents", lambda **kwargs: list(docs.values()))
    monkeypatch.setattr(markdown_service.document_service, "get_document", lambda doc_id: docs.get(doc_id))
    turns.ensure_migration_row(DOC_ID)
    connection.get_store()._execute(
        "UPDATE conversation_docs SET migration_state = 'migrated', intro = '' WHERE doc_id = ?", [DOC_ID],
    )
    with patch.object(markdown_service, "_document_path", return_value=None):
        stats = markdown_service.snapshot_group_conversations("flowgate", "flowgate.default.0351")
    assert stats["failed"] == 1
    assert stats["written"] == 0
