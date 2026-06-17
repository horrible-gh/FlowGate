"""Document-bound query/answer container — integration tests (group 0022 Q/A/V revamp).

Covers DB0006 §3/§4 + L0007 §3 against a real (in-memory file) sqlite with all migrations:
  - ensure_container lazy create + idempotency (keyed by doc_id, q_id := doc_id)
  - add_questions human / ai (asker_kind, title), done→pending re-open
  - register_answer atomicity + author_kind/author_id (AI → author_id NULL)
  - status pending→done when all items answered
  - qa_bundle_by_doc / list_open_items
  - migration 040 retires Q/A/V doc types (is_active=0)
"""
from __future__ import annotations

import os
import sys
import sqlite3
import tempfile
from contextlib import contextmanager
from pathlib import Path

import pytest

os.environ["TESTING"] = "1"
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")

_SERVER_DIR = Path(__file__).resolve().parents[1]
_SCHEMA_DIR = _SERVER_DIR / "sql" / "migrations" / "sqlite"
_QUERIES_JSON = _SERVER_DIR / "sql" / "queries" / "queries.json"
sys.path.insert(0, str(_SERVER_DIR))

import json as _json

_QUERIES: dict = {}
raw = _json.loads(_QUERIES_JSON.read_text(encoding="utf-8"))
for section, entries in raw.items():
    if isinstance(entries, dict):
        for key, sql in entries.items():
            if isinstance(sql, str):
                _QUERIES[f"{section}.{key}"] = sql.replace("%s", "?")


class _MockDB:
    def __init__(self, path):
        self._conn = sqlite3.connect(path, check_same_thread=False)
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
        txn = _MockTxn(self._conn)
        try:
            yield txn
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    def close(self):
        self._conn.close()


class _MockTxn:
    def __init__(self, conn):
        self._conn = conn
        self._cur = None

    def execute(self, sql, params=None):
        self._cur = self._conn.execute(sql, params or [])

    def fetchone(self):
        if self._cur is None:
            return None
        row = self._cur.fetchone()
        return dict(row) if row else None

    def fetchall(self):
        if self._cur is None:
            return []
        return [dict(r) for r in self._cur.fetchall()]


@pytest.fixture()
def store():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    mock_db = _MockDB(path)
    for sql_file in sorted(_SCHEMA_DIR.glob("*.sql")):
        try:
            mock_db._conn.executescript(sql_file.read_text(encoding="utf-8"))
        except Exception:
            pass
    mock_db._conn.commit()

    from modules.flow_gate.db import connection as conn_mod
    original = conn_mod.STORE

    class _PatchedStore(conn_mod.FlowGateStore):
        def __init__(self):
            self._db = mock_db
            self._sq = None

        def _sql(self, key):
            return _QUERIES[key]

    conn_mod.STORE = _PatchedStore()

    # minimal fixtures: project, user, group, doc
    from modules.flow_gate.db import projects, users, groups, documents as db_docs
    projects.create({"project_id": "p", "project_name": "P"})
    users.create({"user_id": "u1", "username": "u1", "email": "u1@e", "password": "x"})
    groups.create({"group_id": "p.none.0001", "project_id": "p", "module": "none", "title": "G"})
    md = Path(path).with_suffix(".doc.md")
    md.write_text("# d", encoding="utf-8")
    store_obj = conn_mod.STORE
    now = "2026-06-13T00:00:00Z"
    store_obj._execute(
        "INSERT OR IGNORE INTO document_types (project_id,type_code,type_name,series,is_system,is_active,sort_order,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
        [None, "D", "Design", "design", 1, 1, 0, now, now],
    )
    db_docs.create({
        "doc_id": "p.none.0001.0001-D", "project_id": "p", "type_code": "D", "seq": 1,
        "title": "Doc One", "group_id": "p.none.0001", "module": "none",
        "owner_id": "u1", "file_path": str(md), "status": "open",
    })
    yield mock_db
    conn_mod.STORE = original
    mock_db.close()
    os.unlink(path)


DOC = "p.none.0001.0001-D"


def test_ensure_container_lazy_and_idempotent(store):
    from modules.flow_gate.services import q_service
    c1 = q_service.ensure_container(DOC, created_by="u1")
    assert c1["doc_id"] == DOC
    assert c1["q_id"] == DOC          # DB0006 §3.2: q_id := doc_id
    c2 = q_service.ensure_container(DOC, created_by="u1")
    assert c2["id"] == c1["id"]       # same row, no duplicate
    rows = store.fetch_all("SELECT id FROM questions WHERE doc_id=?", [DOC])
    assert len(rows) == 1


def test_add_questions_human_then_answer_transitions_done(store):
    from modules.flow_gate.services import q_service
    res = q_service.add_questions(DOC, [{"title": "팔레트", "body": "A or B?"}],
                                  asker_kind="human", created_by="u1")
    item_id = res["added_item_ids"][0]
    bundle = q_service.qa_bundle_by_doc(DOC)
    assert bundle[0]["asker_kind"] == "human"
    assert bundle[0]["answer_body"] is None     # awaiting answer

    ans = q_service.register_answer(DOC, item_id, "B로 진행", author_kind="human", author_id="u1")
    assert ans["status"] == "done"              # answering the single item → done
    bundle2 = q_service.qa_bundle_by_doc(DOC)
    assert bundle2[0]["answer_body"] == "B로 진행"
    assert bundle2[0]["author_kind"] == "human"


def test_ai_question_uses_system_user_and_ai_answer_nulls_author(store):
    from modules.flow_gate.services import q_service
    res = q_service.add_questions(DOC, [{"title": "범위", "body": "scope?"}], asker_kind="ai")
    container = store.fetch_one("SELECT created_by FROM questions WHERE doc_id=?", [DOC])
    assert container["created_by"] == "u-system"     # §3.1 reserved system user
    item_id = res["added_item_ids"][0]

    q_service.register_answer(DOC, item_id, "AI says", author_kind="ai", author_id="ignored")
    row = store.fetch_one(
        "SELECT author_kind, author_id FROM answers WHERE question_item_id=?", [item_id])
    assert row["author_kind"] == "ai"
    assert row["author_id"] is None                  # AI → author_id NULL


def test_requestion_reopens_done_container(store):
    from modules.flow_gate.services import q_service
    r1 = q_service.add_questions(DOC, [{"body": "q1"}], asker_kind="human", created_by="u1")
    q_service.register_answer(DOC, r1["added_item_ids"][0], "a1", author_kind="human", author_id="u1")
    assert store.fetch_one("SELECT status FROM questions WHERE doc_id=?", [DOC])["status"] == "done"
    # re-question = new item → done → pending
    q_service.add_questions(DOC, [{"body": "q2"}], asker_kind="human", created_by="u1")
    assert store.fetch_one("SELECT status FROM questions WHERE doc_id=?", [DOC])["status"] == "pending"


def test_list_open_items(store):
    from modules.flow_gate.services import q_service
    q_service.add_questions(DOC, [{"title": "열린질의", "body": "open?"}],
                            asker_kind="human", created_by="u1")
    items = q_service.list_open_items(project_id="p")
    assert any(it["doc_id"] == DOC and it["title"] == "열린질의" for it in items)
    # TR0005 rework: each row carries the host document's type_code so the dashboard
    # opens the real document (not a Q-tree viewer). DOC is a 'D' document.
    row = next(it for it in items if it["doc_id"] == DOC)
    assert row["type_code"] == "D"
    # once answered, it drops off the list
    bundle = q_service.get_qa_detail(DOC)
    item_id = bundle["items"][0]["id"]
    q_service.register_answer(DOC, item_id, "answered", author_kind="human", author_id="u1")
    items2 = q_service.list_open_items(project_id="p")
    assert not any(it["doc_id"] == DOC for it in items2)


def test_migration_040_retires_qav(store):
    rows = store.fetch_all(
        "SELECT type_code, is_active FROM document_types WHERE type_code IN ('Q','A','V')")
    assert rows  # rows preserved
    assert all(r["is_active"] == 0 for r in rows)
