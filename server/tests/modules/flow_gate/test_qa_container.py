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

_SERVER_DIR = Path(__file__).resolve().parents[3]
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
        # Returns the cursor like the real sqlite adapter, so FlowGateStore._execute_affected
        # (the guarded answer_count increment) can read rowcount.
        self._cur = self._conn.execute(sql, params or [])
        return self._cur

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


def test_open_q_doc_ids_uses_real_unanswered_container_items(store):
    from modules.flow_gate.services import ai_invoke_service, q_service

    added = q_service.add_questions(
        DOC,
        [{"body": "first?"}, {"body": "second?"}],
        asker_kind="human",
        created_by="u1",
    )
    assert ai_invoke_service._open_q_doc_ids("p.none.0001") == [DOC]

    q_service.register_answer(
        DOC, added["added_item_ids"][0], "first answer",
        author_kind="human", author_id="u1",
    )
    assert ai_invoke_service._open_q_doc_ids("p.none.0001") == [DOC]

    q_service.register_answer(
        DOC, added["added_item_ids"][1], "second answer",
        author_kind="human", author_id="u1",
    )
    assert ai_invoke_service._open_q_doc_ids("p.none.0001") == []


def test_open_q_doc_ids_batch_preserves_groups_documents_and_answer_state(store):
    from modules.flow_gate.db import connection as conn_mod
    from modules.flow_gate.db import documents as db_docs, groups
    from modules.flow_gate.services import ai_invoke_service, q_service

    groups.create({"group_id": "p.none.0002", "project_id": "p", "module": "none", "title": "G2"})

    def create_doc(group_id, seq):
        doc_id = f"{group_id}.{seq:04d}-D"
        db_docs.create({
            "doc_id": doc_id, "project_id": "p", "type_code": "D", "seq": seq,
            "title": doc_id, "group_id": group_id, "module": "none",
            "owner_id": "u1", "file_path": f"{doc_id}.md", "status": "open",
        })
        return doc_id

    pending = create_doc("p.none.0001", 2)
    answered = create_doc("p.none.0001", 3)
    mixed = create_doc("p.none.0002", 1)
    duplicate_source = create_doc("p.none.0002", 2)
    no_questions = create_doc("p.none.0002", 3)

    q_service.add_questions(pending, [{"body": "open"}], asker_kind="human", created_by="u1")
    answered_item = q_service.add_questions(
        answered, [{"body": "closed"}], asker_kind="human", created_by="u1",
    )["added_item_ids"][0]
    q_service.register_answer(answered, answered_item, "done", author_kind="human", author_id="u1")
    mixed_items = q_service.add_questions(
        mixed, [{"body": "done"}, {"body": "open"}], asker_kind="human", created_by="u1",
    )["added_item_ids"]
    q_service.register_answer(mixed, mixed_items[0], "done", author_kind="human", author_id="u1")
    q_service.add_questions(
        duplicate_source, [{"body": "open 1"}, {"body": "open 2"}],
        asker_kind="human", created_by="u1",
    )

    original_fetch_all = conn_mod.STORE._fetch_all
    pending_reads = []

    def counted_fetch_all(sql, params=None):
        if "JOIN question_items qi" in sql and "d.group_id IN" in sql:
            pending_reads.append((sql, list(params or [])))
        return original_fetch_all(sql, params)

    conn_mod.STORE._fetch_all = counted_fetch_all
    try:
        result = ai_invoke_service._open_q_doc_ids_by_groups(
            ["p.none.0001", "p.none.0002", "p.none.0001"],
        )
    finally:
        conn_mod.STORE._fetch_all = original_fetch_all

    assert result == {
        "p.none.0001": [pending],
        "p.none.0002": [mixed, duplicate_source],
    }
    assert no_questions not in result["p.none.0002"]
    assert len(pending_reads) == 1
    assert pending_reads[0][1] == ["p.none.0001", "p.none.0002"]












# ── Q 선택지 (group 0243 R0001 / D0006 / DB0007 / L0008) ────────────────────────
