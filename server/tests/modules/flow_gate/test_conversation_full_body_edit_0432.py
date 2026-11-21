"""Block full-body replacement of migrated CH conversations through session save routes (0432).

Follow-up to the earlier implementation: conversation_turns is canonical (0432.0003-NR), and
GET document content projects those turns. PATCH lacked the conversation guard, so it
saved an unread shell file and reported success while the visible conversation stayed
unchanged (NR §5/§7-1). The rejected 0344.0008-TR attempt had no subsequent fix (NR §4).

Apply the exact 0344.0005-L §2-16 condition: conversation type and migration_state=migrated.
Pending/in_progress/failed conversations still use canonical files and remain editable.
Use the session-route HTTPException(detail=...) envelope (0344.0004-P §0-5), not the
worker ok/error_message envelope, to preserve client error handling.

Exercise the guard against fully migrated SQLite rather than mocking migration_state.
"""
from __future__ import annotations

import os
import sqlite3
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest
from fastapi import HTTPException

os.environ["TESTING"] = "1"
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")

_SERVER_DIR = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_SERVER_DIR))

# Literal contract from 0344.0005-L §2-16. Do not import the constant: sharing it
# would let the test and implementation agree on wrong wording. Pin the UI wire contract.
_MESSAGE = (
    "This conversation no longer accepts a full-body edit. "
    "Append one turn: POST /api/v1/conversation/{doc_id}/turn"
)

_PROJECT = "testfbe"
_GROUP = "testfbe-__ALL__-0001"
_OWNER = "usr_fbe_001"


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
def store(migrated_sqlite_db):
    """Install a fully migrated SQLite store as the live STORE."""
    from modules.flow_gate.db import connection

    db_path = migrated_sqlite_db("conversation_full_body_edit_0432.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")

    class _Store(connection.FlowGateStore):
        def __init__(self):
            self._db = _DB(conn)
            self._sq = None

    old = connection.STORE
    connection.STORE = _Store()
    try:
        from modules.flow_gate.db import groups as db_groups, projects, users
        if projects.get_by_id(_PROJECT) is None:
            projects.create({"project_id": _PROJECT, "project_name": "Full-body edit"})
        if users.get_by_id(_OWNER) is None:
            users.create({
                "user_id": _OWNER,
                "username": "fbe_user",
                "email": "fbe@example.com",
                "password": "hashed_pw",
            })
        if db_groups.get_by_id(_GROUP) is None:
            db_groups.create({
                "group_id": _GROUP,
                "project_id": _PROJECT,
                "module": "__ALL__",
                "title": "Full-body edit group",
            })
        yield conn
    finally:
        connection.STORE = old
        conn.close()


def _seed_doc(doc_id: str, stored_path: Path, *, type_code: str = "CH") -> dict:
    from modules.flow_gate.db import documents as db_docs

    stored_path.parent.mkdir(parents=True, exist_ok=True)
    stored_path.write_text("# Original body", encoding="utf-8")
    db_docs.create({
        "doc_id": doc_id,
        "project_id": _PROJECT,
        "type_code": type_code,
        "seq": 1,
        "title": doc_id,
        "group_id": _GROUP,
        "module": "__ALL__",
        "owner_id": _OWNER,
        "file_path": str(stored_path),
        "revision_no": 0,
    })
    return {
        "doc_id": doc_id,
        "project_id": _PROJECT,
        "group_id": _GROUP,
        "type_code": type_code,
        "status": "closed",
        "doc_review_status": "wf_in_progress",
        "file_path": str(stored_path),
    }


def _mark_migrated(doc_id: str) -> None:
    from modules.flow_gate.db import conversation_turns

    assert conversation_turns.acquire_migration_lock(doc_id, "test-owner")
    conversation_turns.mark_migrated(doc_id, "test-owner", "intro", 0)
    assert conversation_turns.migration_state(doc_id) == "migrated"


def _save(monkeypatch, doc: dict, content: str = "wholesale rewrite"):
    """Call the router directly, as test_document_content_editability.py does.
    The conversation decision still reads the real database in the store fixture."""
    from modules.flow_gate.documents.routers import documents as routes

    monkeypatch.setattr(routes.document_service, "get_document", lambda _doc_id: doc)
    monkeypatch.setattr(
        routes.document_service, "update_document",
        lambda _doc_id, _updates, actor_user_id=None: doc,
    )
    monkeypatch.setattr(routes, "_document_file_path", lambda _doc: Path(doc["file_path"]))
    return routes.update_document_content(
        doc["doc_id"],
        routes.DocumentContentUpdate(content=content),
        {"user_id": _OWNER},
    )


def test_migrated_conversation_rejects_a_full_body_save(store, monkeypatch, tmp_path):
    doc = _seed_doc("testfbe-__ALL__-0001-CH0001", tmp_path / "ch1.md")
    _mark_migrated(doc["doc_id"])

    with pytest.raises(HTTPException) as exc_info:
        _save(monkeypatch, doc)

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == _MESSAGE.format(doc_id=doc["doc_id"])
    # Do not touch even the shell file.
    assert Path(doc["file_path"]).read_text(encoding="utf-8") == "# Original body"


def test_the_rpc_wrapper_takes_the_same_block(store, monkeypatch, tmp_path):
    """The RPC PATCH documents/content path uses the same guard and returns the same 409.
    Old bookmarks and direct callers still reach it even if the UI button disappears."""
    from modules.flow_gate.documents.routers import documents as routes

    doc = _seed_doc("testfbe-__ALL__-0001-CH0002", tmp_path / "ch2.md")
    _mark_migrated(doc["doc_id"])

    monkeypatch.setattr(routes.document_service, "get_document", lambda _doc_id: doc)
    monkeypatch.setattr(routes, "_document_file_path", lambda _doc: Path(doc["file_path"]))

    with pytest.raises(HTTPException) as exc_info:
        routes.update_document_content_rpc(
            routes.DocumentContentUpdateRpc(doc_id=doc["doc_id"], content="rewrite"),
            {"user_id": _OWNER},
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == _MESSAGE.format(doc_id=doc["doc_id"])
    assert Path(doc["file_path"]).read_text(encoding="utf-8") == "# Original body"


def test_legacy_conversation_still_saves(store, monkeypatch, tmp_path):
    """Without a conversation_docs row, the conversation is pending and its canonical file remains writable."""
    from modules.flow_gate.db import conversation_turns

    doc = _seed_doc("testfbe-__ALL__-0001-CH0003", tmp_path / "ch3.md")
    assert conversation_turns.migration_state(doc["doc_id"]) == "pending"

    result = _save(monkeypatch, doc, content="legacy edit lands")

    assert result["content"] == "legacy edit lands"
    assert Path(doc["file_path"]).read_text(encoding="utf-8") == "legacy edit lands"


def test_failed_migration_conversation_still_saves(store, monkeypatch, tmp_path):
    """Failed migration is not migrated: retain writes to the still-canonical file."""
    from modules.flow_gate.db import conversation_turns

    doc = _seed_doc("testfbe-__ALL__-0001-CH0004", tmp_path / "ch4.md")
    assert conversation_turns.acquire_migration_lock(doc["doc_id"], "test-owner")
    conversation_turns.mark_failed(doc["doc_id"], "test-owner", "boom")
    assert conversation_turns.migration_state(doc["doc_id"]) == "failed"

    result = _save(monkeypatch, doc, content="failed-migration edit lands")

    assert result["content"] == "failed-migration edit lands"


def test_non_conversation_document_still_saves(store, monkeypatch, tmp_path):
    """Leave non-conversation documents unaffected (regression)."""
    doc = _seed_doc("testfbe-__ALL__-0001-R0001", tmp_path / "r1.md", type_code="R")

    result = _save(monkeypatch, doc, content="ordinary edit lands")

    assert result["content"] == "ordinary edit lands"
    assert Path(doc["file_path"]).read_text(encoding="utf-8") == "ordinary edit lands"
