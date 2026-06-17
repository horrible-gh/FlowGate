"""Q/A T-API write endpoints — worker-token auth (group 0022 TR0009 rev0 rejection regression).

Rejection reason: the guide (Q document guide) tells the worker to register a question by calling
POST /q/{doc_id}/questions with an inbox/edit token, but the endpoint only validated
get_current_user (login-session JWT), so worker tokens were rejected with 401 → the AI worker
couldn't leave ambiguities as a Q and guessed instead.

Fix (the subject of this test): the question/answer registration endpoints also accept inbox/edit tokens.
  - validate the edit-scoped token via token_service.verify
  - allow only when the token's doc_ref matches the path {doc_id} (context binding)
  - force asker_kind/author_kind to 'ai' (the worker only registers AI questions/answers)
  - permission is perm_document_create based on the token's issued_to (isolated here via a has_permission patch)

Environment: TESTING=1 (temporary SQLite, no sqloader) — mirrors the test_inbox.py harness.
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

os.environ["TESTING"] = "1"
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
os.environ["FLOWGATE_TOKEN_PEPPER_ACTIVE_ID"] = "test1"
os.environ["FLOWGATE_TOKEN_PEPPER_test1"] = "test-pepper-value-123"

_SERVER_DIR = Path(__file__).resolve().parents[1]
_SCHEMA_DIR = _SERVER_DIR / "sql" / "migrations" / "sqlite"
_QUERIES_JSON = _SERVER_DIR / "sql" / "queries" / "queries.json"
sys.path.insert(0, str(_SERVER_DIR))

import json as _json

_QUERIES: dict = {}
for _section, _entries in _json.loads(_QUERIES_JSON.read_text(encoding="utf-8")).items():
    if isinstance(_entries, dict):
        for _key, _sql in _entries.items():
            if isinstance(_sql, str):
                _QUERIES[f"{_section}.{_key}"] = _sql.replace("%s", "?")


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

    def execute(self, sql: str, params=None):
        self._cur = self._conn.execute(sql, params or [])

    def fetch_one(self):
        if self._cur is None:
            return None
        row = self._cur.fetchone()
        return dict(row) if row else None

    def fetch_all(self):
        if self._cur is None:
            return []
        return [dict(r) for r in self._cur.fetchall()]

    # q_service uses fetchone/fetchall in places; provide both spellings
    fetchone = fetch_one
    fetchall = fetch_all


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
    original = conn_mod.STORE

    class _PatchedStore(conn_mod.FlowGateStore):
        def __init__(self):
            self._db = mock_db
            self._sq = None

        def _sql(self, key: str) -> str:
            return _QUERIES[key]

    conn_mod.STORE = _PatchedStore()
    yield
    conn_mod.STORE = original


PROJECT = "testprj"
GROUP = "testprj-__ALL__-0001"
DOC = "testprj-__ALL__-0001-D0001"
OTHER_DOC = "testprj-__ALL__-0001-D0002"
USER = "usr_test_001"


@pytest.fixture(scope="module")
def seed_data(tmp_db):
    from modules.flow_gate.db import projects, users, groups, documents as db_docs
    from modules.flow_gate.db.connection import get_store, now_iso

    projects.create({"project_id": PROJECT, "project_name": "Test Project"})
    users.create({"user_id": USER, "username": "t", "email": "t@e", "password": "x"})
    store = get_store()
    now = now_iso()
    store._execute(
        "INSERT OR IGNORE INTO document_types "
        "(project_id,type_code,type_name,series,is_system,is_active,sort_order,created_at,updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        [None, "D", "Design", "design", 1, 1, 0, now, now],
    )
    groups.create({"group_id": GROUP, "project_id": PROJECT, "module": "__ALL__", "title": "G"})
    for seq, doc_id in ((1, DOC), (2, OTHER_DOC)):
        db_docs.create({
            "doc_id": doc_id, "project_id": PROJECT, "type_code": "D", "seq": seq,
            "title": doc_id, "group_id": GROUP, "module": "__ALL__",
            "owner_id": USER, "status": "open",
        })
    yield


def _edit_token(doc_ref: str, tmp_path) -> str:
    """Issue an inbox/edit token (the token a worker actually holds)."""
    from modules.flow_gate.services import token_service
    with patch.object(token_service, "_scratch_dir", return_value=tmp_path / "scratch"):
        result = token_service.issue(
            project=PROJECT, group_id=GROUP, action_scope="edit",
            doc_ref=doc_ref, issued_to=USER,
        )
    return result["raw_token"]


def _client():
    from starlette.testclient import TestClient
    from fastapi import FastAPI
    from modules.flow_gate.api.v1 import q_tapi_routes
    app = FastAPI()
    app.include_router(q_tapi_routes.router)
    return TestClient(app, raise_server_exceptions=True)


# ── Core regression: a worker token can register a Q ───────────────────────────────────

def test_worker_token_can_register_question(seed_data, tmp_path):
    """Exact repro of the rejection: inbox/edit token + POST /q/{doc_id}/questions → 200 ok."""
    raw = _edit_token(DOC, tmp_path)
    with patch("modules.flow_gate.api.v1.q_tapi_routes.has_permission", return_value=True):
        resp = _client().post(
            f"/api/v1/q/{DOC}/questions",
            json={"asker_kind": "human", "questions": [{"title": "범위", "body": "scope?"}]},
            headers={"Authorization": f"Bearer {raw}"},
        )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["ok"] is True
    assert len(data["added_item_ids"]) == 1

    # The worker token forces asker_kind to 'ai' (even if the request said 'human').
    from modules.flow_gate.db.connection import get_store
    row = get_store()._db.fetch_one(
        "SELECT qi.asker_kind FROM question_items qi "
        "JOIN questions q ON qi.question_id = q.id WHERE q.doc_id = ?", [DOC])
    assert row["asker_kind"] == "ai"


def test_worker_token_can_register_answer(seed_data, tmp_path):
    """The edit token issued by [Request answer from AI] must also allow storing an answer (same defect class)."""
    from modules.flow_gate.services import q_service
    res = q_service.add_questions(DOC, [{"title": "q", "body": "answer me?"}],
                                  asker_kind="human", created_by=USER, project_id=PROJECT)
    item_id = res["added_item_ids"][0]

    raw = _edit_token(DOC, tmp_path)
    with patch("modules.flow_gate.api.v1.q_tapi_routes.has_permission", return_value=True):
        resp = _client().post(
            f"/api/v1/q/{DOC}/items/{item_id}/answers",
            json={"author_kind": "human", "body": "AI 답변"},
            headers={"Authorization": f"Bearer {raw}"},
        )
    assert resp.status_code == 200, resp.text
    assert resp.json()["ok"] is True
    from modules.flow_gate.db.connection import get_store
    row = get_store()._db.fetch_one(
        "SELECT author_kind, author_id FROM answers WHERE question_item_id = ?", [item_id])
    assert row["author_kind"] == "ai"        # worker token → forced to 'ai'
    assert row["author_id"] is None          # AI → author_id NULL


# ── Guard: reject when the token's doc_ref points to a different document (context binding) ──────────────────────

def test_worker_token_wrong_doc_ref_rejected(seed_data, tmp_path):
    raw = _edit_token(OTHER_DOC, tmp_path)   # token is bound to OTHER_DOC
    with patch("modules.flow_gate.api.v1.q_tapi_routes.has_permission", return_value=True):
        resp = _client().post(
            f"/api/v1/q/{DOC}/questions",    # attempt to register on a different document
            json={"asker_kind": "ai", "questions": [{"body": "x?"}]},
            headers={"Authorization": f"Bearer {raw}"},
        )
    assert resp.status_code == 403
    assert "binding" in resp.json()["error_message"].lower()


# ── Regression: the login-session JWT (human [+Question]) path still works ──────────────────

def test_login_session_still_works_and_keeps_human_kind(seed_data, tmp_path):
    from modules.flow_gate.auth.jwt_service import create_access_token
    from modules.flow_gate.db.connection import get_store
    get_store()._db.execute("UPDATE users SET is_active = 1 WHERE user_id = ?", [USER])
    jwt, _ = create_access_token(USER, "t", [])
    with patch("modules.flow_gate.api.v1.q_tapi_routes.has_permission", return_value=True):
        resp = _client().post(
            f"/api/v1/q/{DOC}/questions",
            json={"asker_kind": "human", "questions": [{"body": "사람 질의?"}]},
            headers={"Authorization": f"Bearer {jwt}"},
        )
    assert resp.status_code == 200, resp.text
    assert resp.json()["ok"] is True


# ── Guard: missing Bearer → 401 ─────────────────────────────────────────────────────

def test_missing_bearer_rejected(seed_data):
    resp = _client().post(
        f"/api/v1/q/{DOC}/questions",
        json={"asker_kind": "ai", "questions": [{"body": "x?"}]},
    )
    assert resp.status_code == 401
