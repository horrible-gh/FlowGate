"""Document GET answers field — document-bound container model (group 0022 Q/A/V revamp).

Cases:
  1. doc + container + items + answers -> returns Q/A pairs
  2. doc + container + items, 0 answers -> A: null
  3. doc, no container -> no answers key
  4. unrelated doc, no container -> no answers key
"""
from __future__ import annotations

import os
import sys
import sqlite3
import tempfile
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest

os.environ["TESTING"] = "1"
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
os.environ.setdefault("FLOWGATE_TOKEN_PEPPER_ACTIVE_ID", "test1")
os.environ.setdefault("FLOWGATE_TOKEN_PEPPER_test1", "test-pepper-value-123")

_SERVER_DIR = Path(__file__).resolve().parents[1]
_SCHEMA_DIR = _SERVER_DIR / "sql" / "migrations" / "sqlite"
_QUERIES_JSON = _SERVER_DIR / "sql" / "queries" / "queries.json"
sys.path.insert(0, str(_SERVER_DIR))

import json as _json

_QUERIES: dict = {}
if _QUERIES_JSON.exists():
    raw = _json.loads(_QUERIES_JSON.read_text(encoding="utf-8"))
    for section, entries in raw.items():
        if isinstance(entries, dict):
            for key, sql in entries.items():
                if isinstance(sql, str):
                    _QUERIES[f"{section}.{key}"] = sql.replace("%s", "?")


class _MockDB:
    def __init__(self, db_path: str):
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")

    def execute(self, sql, params=None):
        self._conn.execute(sql, params or [])
        self._conn.commit()

    def fetch_one(self, sql, params=None):
        row = self._conn.execute(sql, params or []).fetchone()
        return dict(row) if row else None

    def fetch_all(self, sql, params=None):
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

    def execute(self, sql, params=None):
        self._last_cursor = self._conn.execute(sql, params or [])
        self._conn.commit()

    def fetchone(self):
        return self.fetch_one()

    def fetchall(self):
        return self.fetch_all()

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
            if key in _QUERIES:
                return _QUERIES[key]
            raise KeyError(f"Query not found: {key}")

    conn_mod.STORE = _PatchedStore()
    yield
    conn_mod.STORE = original_store


@pytest.fixture(scope="module")
def seed(tmp_db, tmp_path_factory):
    from modules.flow_gate.db import documents as db_docs
    from modules.flow_gate.db import groups as db_groups
    from modules.flow_gate.db import projects, users
    from modules.flow_gate.db import questions as db_questions
    from modules.flow_gate.db import question_items as db_question_items
    from modules.flow_gate.db import answers as db_answers
    from modules.flow_gate.db.connection import get_store, now_iso

    storage = tmp_path_factory.mktemp("answers-seed")
    projects.create({"project_id": "qprj", "project_name": "Q Project"})
    users.create({"user_id": "usr_q001", "username": "quser",
                  "email": "q@example.com", "password": "hashed_pw"})

    store = get_store()
    now = now_iso()
    db_groups.create({"group_id": "qprj.none.0001", "project_id": "qprj",
                      "module": "none", "title": "Group"})
    store._execute(
        "INSERT OR IGNORE INTO document_types "
        "(project_id, type_code, type_name, series, is_system, is_active, sort_order, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        [None, "D", "Design", "design", 1, 1, 0, now, now],
    )

    def _mkdoc(doc_id, seq):
        md = storage / f"{doc_id}.md"
        md.write_text("# doc\nbody", encoding="utf-8")
        db_docs.create({
            "doc_id": doc_id, "project_id": "qprj", "type_code": "D", "seq": seq,
            "title": f"Doc {seq}", "group_id": "qprj.none.0001", "module": "none",
            "owner_id": "usr_q001", "file_path": str(md), "status": "open",
        })

    # doc1: container + item + answer
    _mkdoc("qprj.none.0001.0001-D", 1)
    db_questions.insert_container_for_doc("qprj.none.0001.0001-D", "qprj", "Doc 1", "usr_q001")
    c1 = db_questions.get_container_by_doc("qprj.none.0001.0001-D")
    db_question_items.insert(c1["id"], 1, "First question", title="t1", asker_kind="human")
    items = db_question_items.list_by_question(c1["id"])
    db_answers.insert(items[0]["id"], "First answer", author_kind="human", author_id="usr_q001")

    # doc2: container + item, no answer
    _mkdoc("qprj.none.0001.0002-D", 2)
    db_questions.insert_container_for_doc("qprj.none.0001.0002-D", "qprj", "Doc 2", "usr_q001")
    c2 = db_questions.get_container_by_doc("qprj.none.0001.0002-D")
    db_question_items.insert(c2["id"], 1, "Second question", title="t2", asker_kind="human")

    # doc3: no container
    _mkdoc("qprj.none.0001.0003-D", 3)
    # doc4: no container
    _mkdoc("qprj.none.0001.0004-D", 4)
    yield


def _make_client():
    from fastapi import FastAPI
    from starlette.testclient import TestClient
    from modules.flow_gate.api.v1 import document_routes
    app = FastAPI()
    app.include_router(document_routes.router)
    return TestClient(app, raise_server_exceptions=True)


_AUTH_PATCH = "modules.flow_gate.api.v1.document_routes.verify_bearer"


class TestDocResponseAnswers:
    def setup_method(self):
        self.client = _make_client()

    def test_case1_container_with_answer(self, seed):
        with patch(_AUTH_PATCH, return_value={"user_id": "usr_q001"}):
            resp = self.client.get("/api/v1/document/qprj.none.0001.0001-D")
        assert resp.status_code == 200
        data = resp.json()
        assert "answers" in data
        assert len(data["answers"]) == 1
        assert data["answers"][0]["Q"] == "First question"
        assert data["answers"][0]["A"] == "First answer"

    def test_case2_container_no_answer(self, seed):
        with patch(_AUTH_PATCH, return_value={"user_id": "usr_q001"}):
            resp = self.client.get("/api/v1/document/qprj.none.0001.0002-D")
        assert resp.status_code == 200
        data = resp.json()
        assert "answers" in data
        assert data["answers"][0]["Q"] == "Second question"
        assert data["answers"][0]["A"] is None

    def test_case3_no_container_no_answers_key(self, seed):
        with patch(_AUTH_PATCH, return_value={"user_id": "usr_q001"}):
            resp = self.client.get("/api/v1/document/qprj.none.0001.0003-D")
        assert resp.status_code == 200
        assert "answers" not in resp.json()

    def test_case4_unrelated_doc_no_answers_key(self, seed):
        with patch(_AUTH_PATCH, return_value={"user_id": "usr_q001"}):
            resp = self.client.get("/api/v1/document/qprj.none.0001.0004-D")
        assert resp.status_code == 200
        assert "answers" not in resp.json()
