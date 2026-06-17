"""TR0044.0010 rev2 — session-side conversation turn endpoint (HTTP integration).

The AI worker accumulates CH turns via the token-bound inbox edit path; a logged-in
PM viewing the chat has no token, so rev1 left them with a plain document and workflow
buttons (the reject). This exercises the new session endpoint
`POST /documents/{doc_id}/conversation/turn`:

  - a human turn is appended in the shared wire format (L0044.0008 §6), newest at the
    bottom, via the server-side serializer (single source of truth);
  - a second turn appends rather than replaces;
  - non-conversation document types are rejected (400);
  - empty bodies are rejected (422).

Scaffolding mirrors test_inbox_conversation_0044.py.
"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

os.environ["TESTING"] = "1"
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")

_SERVER_DIR = Path(__file__).resolve().parents[1]
_SCHEMA_DIR = _SERVER_DIR / "sql" / "migrations" / "sqlite"
sys.path.insert(0, str(_SERVER_DIR))


class _MockDB:
    def __init__(self, db_path: str):
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")

    def execute(self, sql: str, params=None):
        self._conn.execute(sql, params or [])
        self._conn.commit()

    def fetch_one(self, sql: str, params=None):
        row = self._conn.execute(sql, params or []).fetchone()
        return dict(row) if row else None

    def fetch_all(self, sql: str, params=None):
        return [dict(r) for r in self._conn.execute(sql, params or []).fetchall()]

    @contextmanager
    def begin_transaction(self):
        yield _MockTxn(self._conn)

    def close(self):
        self._conn.close()


class _MockTxn:
    def __init__(self, conn):
        self._conn = conn
        self._last_cursor = None

    def execute(self, sql: str, params=None):
        self._last_cursor = self._conn.execute(sql, params or [])
        self._conn.commit()

    def fetch_one(self):
        if self._last_cursor is None:
            return None
        row = self._last_cursor.fetchone()
        return dict(row) if row else None

    def fetch_all(self):
        if self._last_cursor is None:
            return []
        return [dict(r) for r in self._last_cursor.fetchall()]


@pytest.fixture(scope="module")
def tmp_db():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    mock_db = _MockDB(db_path)
    for sql_file in sorted(_SCHEMA_DIR.glob("*.sql")):
        try:
            mock_db._conn.executescript(sql_file.read_text(encoding="utf-8"))
        except Exception:
            pass
    mock_db._conn.commit()
    yield mock_db, db_path
    mock_db.close()
    os.unlink(db_path)


@pytest.fixture(scope="module", autouse=True)
def patch_store(tmp_db):
    mock_db, _ = tmp_db
    from modules.flow_gate.db import connection as conn_mod

    original_store = conn_mod.STORE

    class _PatchedStore(conn_mod.FlowGateStore):
        def __init__(self):
            self._db = mock_db
            self._sq = None

        def _sql(self, key: str) -> str:
            raise NotImplementedError

    conn_mod.STORE = _PatchedStore()
    yield
    conn_mod.STORE = original_store


@pytest.fixture(scope="module")
def seed_data(tmp_db):
    from modules.flow_gate.db import documents as db_docs
    from modules.flow_gate.db import groups as db_groups
    from modules.flow_gate.db import projects, users
    from modules.flow_gate.db.connection import get_store, now_iso

    projects.create({"project_id": "testprj", "project_name": "Test Project"})
    users.create({
        "user_id": "usr_test_001", "username": "owner",
        "email": "owner@example.com", "password": "hashed_pw",
    })
    store = get_store()
    now = now_iso()
    db_groups.create({
        "group_id": "testprj-__ALL__-0044",
        "project_id": "testprj",
        "module": "__ALL__",
        "title": "Conversation Group",
    })
    store._execute(
        "INSERT OR IGNORE INTO document_types (project_id, type_code, type_name, series, is_system, is_active, sort_order, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
        [None, "R", "Requirement", "general", 1, 1, 1, now, now],
    )
    store._execute(
        "INSERT OR IGNORE INTO document_types (project_id, type_code, type_name, series, is_system, is_active, sort_order, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
        [None, "CH", "대화", "general", 1, 1, 25, now, now],
    )
    db_docs.create({
        "doc_id": "testprj-__ALL__-0044-R0001",
        "project_id": "testprj", "type_code": "R", "seq": 1,
        "title": "Root", "group_id": "testprj-__ALL__-0044",
        "module": "__ALL__", "owner_id": "usr_test_001",
    })
    yield


def _make_ch_doc(doc_id, tmp_path, content="", seq=2):
    from modules.flow_gate.db import documents as db_docs

    target = tmp_path / "docs" / f"{doc_id}.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    db_docs.create({
        "doc_id": doc_id, "project_id": "testprj", "type_code": "CH", "seq": seq,
        "title": doc_id, "group_id": "testprj-__ALL__-0044", "module": "__ALL__",
        "owner_id": "usr_test_001", "file_path": str(target), "status": "open",
        "revision_no": 0,
    })
    db_docs.update(doc_id, {"doc_review_status": "approved"})  # L-AUTO
    return target


@contextmanager
def _build_client(target):
    """Client whose file-path resolution is pinned to *target* (sidesteps the storage
    jail, which would reject a tmp path outside the configured storage root)."""
    from modules.flow_gate.documents.routers import documents as doc_router
    from modules.flow_gate.auth.middleware import get_current_user

    app = FastAPI()
    app.include_router(doc_router.router)
    app.dependency_overrides[get_current_user] = lambda: {"user_id": "usr_test_001"}
    with patch.object(doc_router, "_document_file_path", return_value=target), \
            patch.object(doc_router.storage_paths, "to_storage_relative",
                         side_effect=lambda p, *a, **k: str(p)):
        yield TestClient(app)


def test_turn_appended_in_wire_format(seed_data, tmp_path):
    doc_id = "testprj-__ALL__-0044-CH0002"
    target = _make_ch_doc(doc_id, tmp_path, content="", seq=2)

    with _build_client(target) as client:
        resp = client.post(
            f"/documents/{doc_id}/conversation/turn",
            json={"body": "안녕하세요, 첫 메시지입니다."},
        )
    assert resp.status_code == 201, resp.text
    content = resp.json()["content"]
    # Server-side serializer produced the §6 header for a human (user) turn.
    assert "## 🧑 사용자 · " in content
    assert "안녕하세요, 첫 메시지입니다." in content
    # Persisted to the file, newest at the bottom.
    assert content == target.read_text(encoding="utf-8")


def test_second_turn_appends(seed_data, tmp_path):
    doc_id = "testprj-__ALL__-0044-CH0003"
    target = _make_ch_doc(doc_id, tmp_path, content="", seq=3)

    with _build_client(target) as client:
        client.post(f"/documents/{doc_id}/conversation/turn", json={"body": "first"})
        resp = client.post(f"/documents/{doc_id}/conversation/turn", json={"body": "second"})
    assert resp.status_code == 201, resp.text
    content = resp.json()["content"]
    # Two distinct turns, in order — append, not replace.
    assert content.count("## 🧑 사용자 · ") == 2
    assert content.index("first") < content.index("second")


def test_non_conversation_type_rejected(seed_data, tmp_path):
    # R root is not a conversation document → 400, no file write attempted.
    with _build_client(tmp_path / "unused.md") as client:
        resp = client.post(
            "/documents/testprj-__ALL__-0044-R0001/conversation/turn",
            json={"body": "hi"},
        )
    assert resp.status_code == 400, resp.text


def test_empty_body_rejected(seed_data, tmp_path):
    doc_id = "testprj-__ALL__-0044-CH0004"
    target = _make_ch_doc(doc_id, tmp_path, content="", seq=4)
    with _build_client(target) as client:
        # Whitespace-only body: pydantic min_length passes but the handler strips → 422.
        resp = client.post(f"/documents/{doc_id}/conversation/turn", json={"body": "   "})
    assert resp.status_code == 422, resp.text
