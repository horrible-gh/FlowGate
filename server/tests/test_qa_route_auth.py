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
os.environ.setdefault("ALLOWED_ORIGIN", "http://localhost:5173")
os.environ.setdefault("CONTEXT", "/flowgate")
os.environ.setdefault("DB_TYPE", "sqlite")
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
HUMAN_DOC = "testprj-__ALL__-0001-D0003"
AUTO_DOC = "testprj-__ALL__-0001-D0004"
FAIL_DOC = "testprj-__ALL__-0001-D0005"
SLASH_GROUP = "testprj.default.0002"
SLASH_DOC = "testprj.default.0002.0003-D"
USER = "usr_test_001"
AI_RUN_ID = "aiv_route_0598_000001"
AI_PROVENANCE = {
    "ai_run_id": AI_RUN_ID,
    "actual_provider_id": "aip_route_0598",
    "actual_provider_name": "0598 Connected Test Provider",
}
AI_RUN_RECORD = {
    "run_id": AI_RUN_ID,
    "requested_provider_id": AI_PROVENANCE["actual_provider_id"],
    "provider_id": AI_PROVENANCE["actual_provider_id"],
    "provider": {"name": AI_PROVENANCE["actual_provider_name"]},
    "selected_provider_source": "request",
    "attempt_no": 1,
}


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
    groups.create({
        "group_id": SLASH_GROUP, "project_id": PROJECT, "module": "default", "title": "Slash G",
    })
    for seq, doc_id in (
        (1, DOC), (2, OTHER_DOC), (3, HUMAN_DOC), (4, AUTO_DOC), (5, FAIL_DOC),
    ):
        db_docs.create({
            "doc_id": doc_id, "project_id": PROJECT, "type_code": "D", "seq": seq,
            "title": doc_id, "group_id": GROUP, "module": "__ALL__",
            "owner_id": USER, "status": "open",
        })
    db_docs.create({
        "doc_id": SLASH_DOC, "project_id": PROJECT, "type_code": "D", "seq": 3,
        "title": SLASH_DOC, "group_id": SLASH_GROUP, "module": "default",
        "owner_id": USER, "status": "open",
    })
    yield


def _edit_token(doc_ref: str, tmp_path, *, ai_run_id: str | None = None) -> str:
    """Issue an inbox/edit token (the token a worker actually holds)."""
    from modules.flow_gate.services import token_service
    with patch.object(token_service, "_scratch_dir", return_value=tmp_path / "scratch"):
        result = token_service.issue(
            project=PROJECT, group_id=GROUP, action_scope="edit",
            doc_ref=doc_ref, issued_to=USER, ai_run_id=ai_run_id,
        )
    return result["raw_token"]


def _client():
    from starlette.testclient import TestClient
    from fastapi import FastAPI
    from modules.flow_gate.api.v1 import q_tapi_routes
    app = FastAPI()
    app.include_router(q_tapi_routes.router)
    return TestClient(app, raise_server_exceptions=True)


def _add_single_question(doc_id: str, body: str) -> int:
    """Create only the prerequisite Q; every answer under test must enter through HTTP."""
    from modules.flow_gate.services import q_service
    result = q_service.add_questions(
        doc_id, [{"title": "0598 connected", "body": body}],
        asker_kind="human", created_by=USER, project_id=PROJECT,
    )
    return result["added_item_ids"][0]


def _human_token() -> str:
    from modules.flow_gate.auth.jwt_service import create_access_token
    from modules.flow_gate.db.connection import get_store
    get_store()._db.execute("UPDATE users SET is_active = 1 WHERE user_id = ?", [USER])
    return create_access_token(USER, "t", [])[0]


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
    """Worker HTTP answer keeps forced AI attribution and its token-bound run provenance."""
    from modules.flow_gate.db.connection import get_store
    from modules.flow_gate.services import q_service

    item_id = _add_single_question(DOC, "answer me?")
    raw = _edit_token(DOC, tmp_path, ai_run_id=AI_RUN_ID)
    with (
        patch("modules.flow_gate.api.v1.q_tapi_routes.has_permission", return_value=True),
        patch(
            "modules.flow_gate.services.ai_invoke.runtime.get_run_record",
            return_value=dict(AI_RUN_RECORD),
        ) as get_run_record,
    ):
        resp = _client().post(
            f"/api/v1/q/{DOC}/items/{item_id}/answers",
            json={"author_kind": "human", "body": "AI 답변"},
            headers={"Authorization": f"Bearer {raw}"},
        )

    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["ok"] is True
    assert payload["author_kind"] == "ai"
    get_run_record.assert_called_once_with(AI_RUN_ID)

    row = get_store()._db.fetch_one(
        "SELECT id, body, author_kind, author_id, author_ai_run_id, "
        "author_actual_provider_id, author_actual_provider_name "
        "FROM answers WHERE question_item_id = ?", [item_id],
    )
    assert row == {
        "id": payload["answer_id"],
        "body": "AI 답변",
        "author_kind": "ai",
        "author_id": None,
        "author_ai_run_id": AI_RUN_ID,
        "author_actual_provider_id": AI_PROVENANCE["actual_provider_id"],
        "author_actual_provider_name": AI_PROVENANCE["actual_provider_name"],
    }
    item = next(it for it in q_service.get_qa_detail(DOC)["items"] if it["id"] == item_id)
    assert item["answer_count"] == 1
    assert item["answers"][0]["author_provider"] == {
        "ai_run_id": AI_RUN_ID,
        "ai_provider_id": AI_PROVENANCE["actual_provider_id"],
        "ai_provider_name": AI_PROVENANCE["actual_provider_name"],
    }


# ── 0598 connected answer route regressions ──────────────────────────────────────────

def test_http_answer_register_canonical_success(seed_data):
    """Human answer crosses canonical HTTP route and commits answer/count/done state."""
    from modules.flow_gate.db.connection import get_store

    item_id = _add_single_question(HUMAN_DOC, "canonical human?")
    with patch("modules.flow_gate.api.v1.q_tapi_routes.has_permission", return_value=True):
        resp = _client().post(
            f"/api/v1/q/{HUMAN_DOC}/items/{item_id}/answers",
            json={"author_kind": "human", "body": "canonical answer"},
            headers={"Authorization": f"Bearer {_human_token()}"},
        )

    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["ok"] is True
    assert payload["status"] == "done"
    row = get_store()._db.fetch_one(
        "SELECT body, author_kind, author_id, author_ai_run_id, "
        "author_actual_provider_id, author_actual_provider_name "
        "FROM answers WHERE question_item_id = ?", [item_id],
    )
    assert row == {
        "body": "canonical answer",
        "author_kind": "human",
        "author_id": USER,
        "author_ai_run_id": None,
        "author_actual_provider_id": None,
        "author_actual_provider_name": None,
    }
    item = get_store()._db.fetch_one(
        "SELECT answer_count FROM question_items WHERE id = ?", [item_id],
    )
    container = get_store()._db.fetch_one(
        "SELECT status FROM questions WHERE doc_id = ?", [HUMAN_DOC],
    )
    assert item["answer_count"] == 1
    assert container["status"] == "done"


def test_http_last_answer_triggers_auto_resume(seed_data, monkeypatch):
    """The final HTTP answer reaches the 0551 hook with request-derived base and locale."""
    from modules.flow_gate.db.connection import get_store
    from modules.flow_gate.services import q_service

    item_id = _add_single_question(AUTO_DOC, "resume after me?")
    resume_calls = []
    trace = {"requester_run_id": "aiv_requester", "resumed_run_id": "aiv_resumed"}

    def _resume(**kwargs):
        resume_calls.append(kwargs)
        return dict(trace)

    monkeypatch.setattr(q_service, "auto_resume_answered_chain", _resume)
    with patch("modules.flow_gate.api.v1.q_tapi_routes.has_permission", return_value=True):
        resp = _client().post(
            f"/api/v1/q/{AUTO_DOC}/items/{item_id}/answers",
            json={"author_kind": "human", "body": "last answer"},
            headers={"Authorization": f"Bearer {_human_token()}", "x-locale": "ja"},
        )

    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["ok"] is True
    assert payload["status"] == "done"
    assert payload["question_resume_trace"] == trace
    assert resume_calls == [{
        "doc_id": AUTO_DOC,
        "api_base_url": "http://testserver/flowgate/api/v1",
        "locale": "ja",
    }]
    assert get_store()._db.fetch_one(
        "SELECT answer_count FROM question_items WHERE id = ?", [item_id],
    )["answer_count"] == 1
    assert get_store()._db.fetch_one(
        "SELECT status FROM questions WHERE doc_id = ?", [AUTO_DOC],
    )["status"] == "done"


def test_http_answer_register_slash_path_success(seed_data):
    """Slash-path route shares the real persistence path instead of only composing an id."""
    from modules.flow_gate.db.connection import get_store

    item_id = _add_single_question(SLASH_DOC, "slash route?")
    with patch("modules.flow_gate.api.v1.q_tapi_routes.has_permission", return_value=True):
        resp = _client().post(
            f"/api/v1/q/{PROJECT}/default/0002/0003-D/items/{item_id}/answers",
            json={"author_kind": "human", "body": "slash answer"},
            headers={"Authorization": f"Bearer {_human_token()}"},
        )

    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["ok"] is True
    assert payload["doc_id"] == SLASH_DOC
    row = get_store()._db.fetch_one(
        "SELECT body, author_kind, author_id FROM answers WHERE question_item_id = ?", [item_id],
    )
    assert row == {"body": "slash answer", "author_kind": "human", "author_id": USER}
    assert get_store()._db.fetch_one(
        "SELECT answer_count FROM question_items WHERE id = ?", [item_id],
    )["answer_count"] == 1


def test_http_auto_resume_failure_does_not_rollback_answer(seed_data, monkeypatch):
    """A real 0551 best-effort failure happens after the committed HTTP answer."""
    from modules.flow_gate.db.connection import get_store
    from modules.flow_gate.services import q_service

    item_id = _add_single_question(FAIL_DOC, "persist before failed resume?")

    def _resume_dependency_failure(_group_id):
        raise RuntimeError("injected auto-resume dependency failure")

    monkeypatch.setattr(
        q_service.db_questions, "list_open_doc_ids_by_group", _resume_dependency_failure,
    )
    with patch("modules.flow_gate.api.v1.q_tapi_routes.has_permission", return_value=True):
        resp = _client().post(
            f"/api/v1/q/{FAIL_DOC}/items/{item_id}/answers",
            json={"author_kind": "human", "body": "durable answer"},
            headers={"Authorization": f"Bearer {_human_token()}"},
        )

    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["ok"] is True
    assert payload["status"] == "done"
    assert "question_resume_trace" not in payload
    row = get_store()._db.fetch_one(
        "SELECT body, author_kind, author_id FROM answers WHERE question_item_id = ?", [item_id],
    )
    assert row == {"body": "durable answer", "author_kind": "human", "author_id": USER}
    assert get_store()._db.fetch_one(
        "SELECT answer_count FROM question_items WHERE id = ?", [item_id],
    )["answer_count"] == 1
    assert get_store()._db.fetch_one(
        "SELECT status FROM questions WHERE doc_id = ?", [FAIL_DOC],
    )["status"] == "done"


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
