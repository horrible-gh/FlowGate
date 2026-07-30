"""Bulk CH migration driver (group 0351, T4, L0004 §2-14).

A real in-memory SQLite carrying the 074 schema PLUS a `documents` stub wide enough
for the LEFT JOIN judgment (`type_code`, `updated_at`) — the bulk driver's own SQL,
not just document_service, needs real columns here.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

import pytest

from modules.flow_gate.db import connection
from modules.flow_gate.db import conversation_turns as turn_store
from modules.flow_gate.services import conversation_turn_service as append_service
from tools import migrate_conversations_bulk as bulk


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
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute(
        "CREATE TABLE documents(doc_id TEXT PRIMARY KEY, type_code TEXT, "
        "updated_at TEXT DEFAULT '2026-01-01T00:00:00+09:00')"
    )
    sql = Path(__file__).resolve().parents[1] / "sql/migrations/sqlite/074_conversation_turns.sql"
    conn.executescript(sql.read_text(encoding="utf-8"))
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


def _add_doc(doc_id: str, type_code: str = "CH") -> None:
    connection.get_store()._execute(
        "INSERT INTO documents(doc_id, type_code) VALUES (?, ?)", [doc_id, type_code]
    )


def _doc_row(doc_id: str, file_path: str = "") -> dict:
    return {
        "doc_id": doc_id, "type_code": "CH", "title": "대화", "status": "draft",
        "group_id": "flowgate.default.0351", "project_id": "flowgate", "module": "default",
        "triggered_by": "flowgate.default.0351.0001-R", "file_path": file_path,
    }


def _patch_docs(monkeypatch, docs: dict) -> None:
    monkeypatch.setattr(append_service.document_service, "get_document", lambda doc_id: docs.get(doc_id))


# ── LEFT JOIN 판정 (L0004 §2-14) ──────────────────────────────────────────────

def test_pending_docs_have_no_conversation_docs_row_and_are_selected(store):
    _add_doc("flowgate.default.0351.0002-CH")
    _add_doc("flowgate.default.0351.0004-T", type_code="T")  # non-CH must never be selected
    rows = turn_store.list_ch_docs_needing_migration()
    assert [r["doc_id"] for r in rows] == ["flowgate.default.0351.0002-CH"]


def test_migrated_docs_are_excluded_failed_docs_are_included(store):
    _add_doc("flowgate.default.0351.0002-CH")
    _add_doc("flowgate.default.0351.0003-CH")
    turn_store.ensure_migration_row("flowgate.default.0351.0002-CH")
    connection.get_store()._execute(
        "UPDATE conversation_docs SET migration_state = 'migrated' WHERE doc_id = ?",
        ["flowgate.default.0351.0002-CH"],
    )
    turn_store.ensure_migration_row("flowgate.default.0351.0003-CH")
    connection.get_store()._execute(
        "UPDATE conversation_docs SET migration_state = 'failed' WHERE doc_id = ?",
        ["flowgate.default.0351.0003-CH"],
    )
    rows = turn_store.list_ch_docs_needing_migration()
    assert [r["doc_id"] for r in rows] == ["flowgate.default.0351.0003-CH"]


def test_limit_bounds_the_candidate_set(store):
    for i in range(5):
        _add_doc(f"flowgate.default.0351.000{i}-CH")
    rows = turn_store.list_ch_docs_needing_migration(limit=2)
    assert len(rows) == 2


# ── bulk_migrate: 격리 + 집계 (완료 기준: 일괄 이관) ──────────────────────────

def test_bulk_migrate_migrates_every_pending_doc_and_is_idempotent_on_rerun(store, monkeypatch):
    docs = {
        "flowgate.default.0351.0002-CH": _doc_row("flowgate.default.0351.0002-CH"),
        "flowgate.default.0351.0003-CH": _doc_row("flowgate.default.0351.0003-CH"),
    }
    for doc_id in docs:
        _add_doc(doc_id)
    _patch_docs(monkeypatch, docs)

    first = bulk.bulk_migrate()
    assert first["scanned"] == 2
    assert first["migrated"] == 2
    assert first["failed"] == 0
    assert turn_store.migration_state("flowgate.default.0351.0002-CH") == "migrated"
    assert turn_store.migration_state("flowgate.default.0351.0003-CH") == "migrated"

    # Re-run: the LEFT JOIN judgment no longer selects either — no re-migration.
    second = bulk.bulk_migrate()
    assert second["scanned"] == 0
    assert second["migrated"] == 0


def test_one_documents_failure_does_not_block_the_next(store, monkeypatch):
    good_id = "flowgate.default.0351.0002-CH"
    bad_id = "flowgate.default.0351.0003-CH"
    docs = {good_id: _doc_row(good_id), bad_id: _doc_row(bad_id)}
    for doc_id in docs:
        _add_doc(doc_id)
    _patch_docs(monkeypatch, docs)

    real_migrate = append_service.migrate_conversation

    def fake_migrate(doc_id):
        if doc_id == bad_id:
            turn_store.ensure_migration_row(doc_id)
            owner = "test-owner"
            connection.get_store()._execute(
                "UPDATE conversation_docs SET migration_state = 'in_progress', lock_owner = ? "
                "WHERE doc_id = ?", [owner, doc_id],
            )
            turn_store.mark_failed(doc_id, owner, "conversation has more than 5000 turns")
            return False
        return real_migrate(doc_id)

    monkeypatch.setattr(bulk.conversation_turn_service, "migrate_conversation", fake_migrate)

    stats = bulk.bulk_migrate()
    assert stats["scanned"] == 2
    assert stats["migrated"] == 1
    assert stats["failed"] == 1
    assert stats["failures"] == [{"doc_id": bad_id, "reason": "conversation has more than 5000 turns"}]
    assert turn_store.migration_state(good_id) == "migrated"
    assert turn_store.migration_state(bad_id) == "failed"


def test_lock_held_by_another_runner_is_skipped_not_counted_as_failed(store, monkeypatch):
    doc_id = "flowgate.default.0351.0002-CH"
    _add_doc(doc_id)
    _patch_docs(monkeypatch, {doc_id: _doc_row(doc_id)})
    # A concurrent runner holds a fresh (non-stale) lock.
    turn_store.acquire_migration_lock(doc_id, "other-runner")

    monkeypatch.setattr(
        bulk.conversation_turn_service, "migrate_conversation", lambda _doc_id: False
    )
    stats = bulk.bulk_migrate()
    assert stats["scanned"] == 1
    assert stats["migrated"] == 0
    assert stats["failed"] == 0
    assert stats["skipped"] == 1


def test_dry_run_reports_the_candidate_count_without_migrating(store, monkeypatch):
    doc_id = "flowgate.default.0351.0002-CH"
    _add_doc(doc_id)
    _patch_docs(monkeypatch, {doc_id: _doc_row(doc_id)})
    stats = bulk.bulk_migrate(dry_run=True)
    assert stats["scanned"] == 1
    assert stats["migrated"] == 0
    assert turn_store.migration_state(doc_id) == "pending"


def test_limit_caps_documents_processed_in_one_call(store, monkeypatch):
    docs = {}
    for i in range(5):
        doc_id = f"flowgate.default.0351.000{i}-CH"
        _add_doc(doc_id)
        docs[doc_id] = _doc_row(doc_id)
    _patch_docs(monkeypatch, docs)
    stats = bulk.bulk_migrate(limit=2)
    assert stats["scanned"] == 2
    assert stats["migrated"] == 2
