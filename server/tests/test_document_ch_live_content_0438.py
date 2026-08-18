"""flowgate.default.0438: live CH content is the document read canonical."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

os.environ["TESTING"] = "1"
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
_SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SERVER_DIR))

from modules.flow_gate.api.v1 import document_routes, module_routes  # noqa: E402
from modules.flow_gate.db import connection  # noqa: E402
from modules.flow_gate.db import conversation_turns  # noqa: E402
from modules.flow_gate.services import conversation_markdown_service  # noqa: E402
from modules.flow_gate.services import conversation_query_service  # noqa: E402
from modules.flow_gate.services.conversation_turn_service import ConversationTurnError  # noqa: E402

DOC_ID = "flowgate.default.0438.0002-CH"
STALE = "---\ntitle: stale\n---\n\n# Stale snapshot\n\nold file only\n"
INTRO = "---\ntitle: live\n---\n\n# Live Conversation\n"


class _DB:
    def __init__(self, conn):
        self._conn = conn

    def connection(self):
        return self._conn

    def execute(self, sql, params=None):
        return self._conn.execute(sql, params or [])

    def executescript(self, sql):
        return self._conn.executescript(sql)

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()


@pytest.fixture
def store():
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("CREATE TABLE documents(doc_id TEXT PRIMARY KEY)")
    migration = _SERVER_DIR / "sql/migrations/sqlite/074_conversation_turns.sql"
    conn.executescript(migration.read_text(encoding="utf-8"))
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


def _row(file_path: str) -> dict:
    return {
        "doc_id": DOC_ID,
        "type_code": "CH",
        "title": "Live conversation",
        "status": "approved",
        "revision_no": 0,
        "group_id": "flowgate.default.0438",
        "project_id": "flowgate",
        "module": "default",
        "branch": "main",
        "file_path": file_path,
    }


def _seed_live(store, monkeypatch, tmp_path):
    stale_file = tmp_path / "stale.md"
    stale_file.write_text(STALE, encoding="utf-8")
    row = _row(str(stale_file))
    monkeypatch.setattr(document_routes, "verify_bearer", lambda _request: {"ok": True})
    monkeypatch.setattr(document_routes, "get_answers_for_document", lambda _doc_id: [])
    monkeypatch.setattr(document_routes, "_load_reviews", lambda _doc_id: (None, []))
    monkeypatch.setattr(document_routes, "_load_test_runs", lambda _doc_id: (None, []))
    monkeypatch.setattr(document_routes.db_docs, "get_by_id", lambda doc_id: dict(row) if doc_id == DOC_ID else None)
    monkeypatch.setattr(document_routes, "resolve_storage_path", lambda *_args, **_kwargs: stale_file)
    for service in (conversation_markdown_service.document_service, conversation_query_service.document_service):
        monkeypatch.setattr(service, "get_document", lambda doc_id: dict(row) if doc_id == DOC_ID else None)

    conversation_turns.ensure_migration_row(DOC_ID)
    connection.get_store()._execute(
        "UPDATE conversation_docs SET migration_state = 'migrated', intro = ? WHERE doc_id = ?",
        [INTRO, DOC_ID],
    )
    for seq, body in ((1, "live decision alpha"), (2, "live decision beta")):
        conversation_turns.insert_migrated_turn(
            doc_id=DOC_ID,
            seq=seq,
            speaker="user" if seq == 1 else "ai",
            participant_key="user:u1" if seq == 1 else "provider:p1",
            display_name="tester",
            locale="ko" if seq == 1 else None,
            body=body,
            body_hash=hashlib.sha256(body.encode()).hexdigest(),
            based_on_seq=seq - 1,
            idempotency_key=f"seed:{seq}",
            idempotency_hash=hashlib.sha256(f"seed:{seq}".encode()).hexdigest(),
            created_at=f"2026-08-18T06:0{seq}:00+09:00",
        )
    return row, stale_file


def _json(response) -> dict:
    return json.loads(response.body)


def _http_client() -> TestClient:
    app = FastAPI()
    app.include_router(document_routes.router)
    return TestClient(app)


def test_canonical_and_path_get_return_database_turns_before_snapshot(store, monkeypatch, tmp_path):
    _seed_live(store, monkeypatch, tmp_path)
    monkeypatch.setattr(document_routes, "_compose_group_doc_ids", lambda *_args: ("flowgate.default.0438", DOC_ID))
    client = _http_client()
    canonical_response = client.get(f"/api/v1/document/{DOC_ID}")
    path_response = client.get("/api/v1/document/flowgate/branches/main/__ALL__/0438/CH0002")
    assert canonical_response.status_code == path_response.status_code == 200

    for body in (canonical_response.json(), path_response.json()):
        assert body["content"].find("live decision alpha") < body["content"].find("live decision beta")
        assert "old file only" not in body["content"]
        assert body["stored_path"].endswith("stale.md")


def test_outline_section_and_meta_share_the_live_ch_canonical(store, monkeypatch, tmp_path):
    _seed_live(store, monkeypatch, tmp_path)
    client = _http_client()
    outline_response = client.get(f"/api/v1/document/{DOC_ID}/outline")
    section_response = client.get(
        f"/api/v1/document/{DOC_ID}/section", params={"section": "Live Conversation"}
    )
    meta_response = client.get(f"/api/v1/document/{DOC_ID}/meta")
    canonical_response = client.get(f"/api/v1/document/{DOC_ID}")
    assert {outline_response.status_code, section_response.status_code, meta_response.status_code, canonical_response.status_code} == {200}
    outline = outline_response.json()
    section = section_response.json()
    meta = meta_response.json()
    canonical = canonical_response.json()

    assert any(item["title"] == "Live Conversation" for item in outline["items"])
    assert "live decision alpha" in section["text"] and "live decision beta" in section["text"]
    assert meta["body"]["content_sha256"] == outline["content_sha256"]
    assert meta["body"]["chars"] == len(canonical["content"])


def test_non_ch_and_failed_ch_keep_file_bytes_and_render_failure_falls_back(store, monkeypatch, tmp_path):
    row, stale_file = _seed_live(store, monkeypatch, tmp_path)
    non_ch = {**row, "type_code": "T"}
    assert document_routes._resolve_live_content(non_ch) == STALE

    connection.get_store()._execute(
        "UPDATE conversation_docs SET migration_state = 'failed' WHERE doc_id = ?", [DOC_ID]
    )
    assert document_routes._resolve_live_content(row) == STALE

    connection.get_store()._execute(
        "UPDATE conversation_docs SET migration_state = 'migrated' WHERE doc_id = ?", [DOC_ID]
    )
    monkeypatch.setattr(
        document_routes.conversation_markdown_service,
        "render_markdown",
        lambda _doc_id: (_ for _ in ()).throw(ConversationTurnError(500, "render failed")),
    )
    assert document_routes._resolve_live_content(row) == stale_file.read_text(encoding="utf-8")


def test_predecessor_contract_exposes_live_ch_status(store, monkeypatch, tmp_path):
    row, _ = _seed_live(store, monkeypatch, tmp_path)
    monkeypatch.setattr(module_routes, "verify_bearer", lambda _request: {"ok": True})
    monkeypatch.setattr(module_routes.db_wfseq, "get_sequence_by_doc_id", lambda _doc_id: {"id": 7})
    monkeypatch.setattr(module_routes.db_wfseq, "get_effective_head", lambda _seq_id: {"id": 9})
    monkeypatch.setattr(
        module_routes.db_wfseq,
        "get_predecessor_result_doc_ids",
        lambda *_args, **_kwargs: [DOC_ID],
    )
    monkeypatch.setattr(module_routes.db_docs, "get_by_id", lambda doc_id: dict(row) if doc_id == DOC_ID else None)

    body = _json(module_routes.list_predecessor_docs(None, "flowgate.default.0438.0001-B"))
    assert body["predecessor_doc_ids"] == [DOC_ID]
    assert body["predecessors"][0]["conversation"] == {
        "migration_state": "migrated",
        "turn_count": 2,
        "live_content": True,
    }


class _UnreadablePath:
    def read_text(self, *, encoding):
        raise OSError(13, "Permission denied")


def test_non_ch_os_read_failure_preserves_explicit_500(store, monkeypatch, tmp_path):
    row, _ = _seed_live(store, monkeypatch, tmp_path)
    non_ch = {**row, "type_code": "T"}
    monkeypatch.setattr(
        document_routes.db_docs,
        "get_by_id",
        lambda doc_id: dict(non_ch) if doc_id == DOC_ID else None,
    )
    monkeypatch.setattr(
        document_routes, "_compose_group_doc_ids", lambda *_args: ("flowgate.default.0438", DOC_ID)
    )
    monkeypatch.setattr(document_routes, "resolve_storage_path", lambda *_args, **_kwargs: _UnreadablePath())

    client = _http_client()
    responses = [
        client.get(f"/api/v1/document/{DOC_ID}"),
        client.get("/api/v1/document/flowgate/branches/main/__ALL__/0438/T0002"),
    ]

    for response in responses:
        assert response.status_code == 500
        assert response.json()["error_message"] == "An error occurred while reading the document content"


def test_outline_section_and_meta_os_read_failure_preserves_explicit_500(store, monkeypatch, tmp_path):
    row, _ = _seed_live(store, monkeypatch, tmp_path)
    non_ch = {**row, "type_code": "T"}
    monkeypatch.setattr(
        document_routes.db_docs,
        "get_by_id",
        lambda doc_id: dict(non_ch) if doc_id == DOC_ID else None,
    )
    monkeypatch.setattr(document_routes, "resolve_storage_path", lambda *_args, **_kwargs: _UnreadablePath())

    client = _http_client()
    responses = [
        client.get(f"/api/v1/document/{DOC_ID}/outline"),
        client.get(f"/api/v1/document/{DOC_ID}/section", params={"section": "Anything"}),
        client.get(f"/api/v1/document/{DOC_ID}/meta"),
    ]

    for response in responses:
        assert response.status_code == 500
        body = response.json()
        assert body["ok"] is False
        assert body["error_message"] == "An error occurred while reading the document content"
        assert "help_url" in body


def test_migrated_ch_render_and_file_double_failure_keeps_null_fallback(
    store, monkeypatch, tmp_path
):
    _seed_live(store, monkeypatch, tmp_path)
    monkeypatch.setattr(
        document_routes.conversation_markdown_service,
        "render_markdown",
        lambda _doc_id: (_ for _ in ()).throw(ConversationTurnError(500, "render failed")),
    )
    monkeypatch.setattr(document_routes, "resolve_storage_path", lambda *_args, **_kwargs: _UnreadablePath())

    response = _http_client().get(f"/api/v1/document/{DOC_ID}")

    assert response.status_code == 200
    assert response.json()["content"] is None