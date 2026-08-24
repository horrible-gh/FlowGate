"""CH (conversation) documents take no queries — B0001 / NR0004 (group 0261) regression.

Bug (B0001): the AI, mid-conversation, announced it would register its uncertainty as a Q.
A chat has no Q surface — DocInfoPanel is switched off for CH (MainPanel.canShowDocInfoPanel,
TR0044.0010 rev8) — yet nothing on the server refused the registration, so a Q posted from a
chat would have been stored and rendered nowhere.

Fix (NR0004 §6, approved): POST /q/{doc_id}/questions rejects a CH target with 400, and the
message tells the worker to ask in its reply turn instead.

The gate is on the TARGET DOCUMENT rather than the credential because a "chat token" is not
observable server-side: BOTH chat paths send the wire scope "chat" and both map it onto the
edit grant before issuing (ai_invoke_routes._TOKEN_SCOPE, and since 0293
token_routes._WIRE_TOKEN_SCOPE for the manual [멘트복사] path). "chat" selects a mention, never
a grant, so the issued token is indistinguishable from any other edit token. What the two paths
do share is the CH document they are bound to — hence these tests issue an ordinary token
(exactly what a chat worker holds) and assert on the document type.

Environment: TESTING=1 (temporary SQLite, no sqloader) — mirrors the test_q_anchor_0059.py harness.
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
    # Environment repair, NOT part of the fix under test (TR0006 §4): the sqlite profile's
    # migration 064 rebuilds `tokens` to widen the action_scope CHECK and its column list
    # omits continuation_instruction_mode, so 063_tokens_continuation_instruction_mode is
    # undone by the very next migration. token_service.issue() always INSERTs that column,
    # so without this line no token can be minted here and the worker-token cases below
    # cannot run at all. Postgres (the live profile) is unaffected — its 064 swaps the
    # constraint in place. Filed separately; drop this once the migration is repaired.
    try:
        mock_db._conn.execute("ALTER TABLE tokens ADD COLUMN continuation_instruction_mode TEXT")
    except sqlite3.OperationalError:
        pass  # already present — migration repaired
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


PROJECT = "chprj"
GROUP = "chprj-__ALL__-0261"
USER = "usr_ch_001"

SPINE = "chprj-__ALL__-0261-B0001"   # the bug the conversation hangs off
CHDOC = "chprj-__ALL__-0261-CH0002"  # the conversation the chat worker is bound to
TRDOC = "chprj-__ALL__-0261-TR0005"  # an ordinary artifact — the non-CH regression target


def _doc_type(type_code: str):
    from modules.flow_gate.db.connection import get_store, now_iso
    store = get_store()
    now = now_iso()
    store._execute(
        "INSERT OR IGNORE INTO document_types "
        "(project_id,type_code,type_name,series,is_system,is_active,sort_order,created_at,updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        [None, type_code, type_code, "x", 1, 1, 0, now, now],
    )


def _mk_doc(doc_id: str, type_code: str, seq: int, review_status: str = "wf_in_progress"):
    from modules.flow_gate.db import documents as db_docs
    from modules.flow_gate.db.connection import get_store
    db_docs.create({
        "doc_id": doc_id, "project_id": PROJECT, "type_code": type_code, "seq": seq,
        "title": doc_id, "group_id": GROUP, "module": "__ALL__",
        "owner_id": USER, "status": "open",
    })
    get_store()._execute(
        "UPDATE documents SET doc_review_status = ? WHERE doc_id = ?",
        [review_status, doc_id],
    )


@pytest.fixture(scope="module")
def seed_data(tmp_db):
    from modules.flow_gate.db import projects, users, groups

    projects.create({"project_id": PROJECT, "project_name": "CH Project"})
    users.create({"user_id": USER, "username": "c", "email": "c@e", "password": "x"})
    for tc in ("B", "CH", "WP", "T", "TR"):
        _doc_type(tc)
    groups.create({"group_id": GROUP, "project_id": PROJECT, "module": "__ALL__", "title": "G"})

    _mk_doc(SPINE, "B", 1, review_status="wf_in_progress")
    _mk_doc(CHDOC, "CH", 2, review_status="approved")
    _mk_doc(TRDOC, "TR", 5, review_status="pending_review")
    yield


def _seed_sequence(spine: str, items: list[tuple[str, int, str | None]]):
    from modules.flow_gate.db import workflow_sequences as db_wfseq
    from modules.flow_gate.db.connection import get_store

    store = get_store()
    store._execute("INSERT INTO workflow_sequences (doc_id) VALUES (?)", [spine])
    seq = db_wfseq.get_sequence_by_doc_id(spine)
    for item_seq, (type_code, sort_order, result_doc_id) in enumerate(items, start=1):
        store._execute(
            "INSERT INTO workflow_sequence_items "
            "(sequence_id,item_seq,type,label,doc_class,sort_order,result_doc_id) "
            "VALUES (?,?,?,?,?,?,?)",
            [seq["id"], item_seq, type_code, type_code, "B", sort_order, result_doc_id],
        )
    return seq


def _worker_token(doc_ref: str, tmp_path, action_scope: str = "edit") -> str:
    """Issue the same document-bound token used by chat/edit and new-document workers."""
    from modules.flow_gate.services import token_service
    with patch.object(token_service, "_scratch_dir", return_value=tmp_path / "scratch"):
        result = token_service.issue(
            project=PROJECT, group_id=GROUP, action_scope=action_scope,
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


def _container_exists(doc_id: str) -> bool:
    from modules.flow_gate.db.connection import get_store
    return get_store()._db.fetch_one("SELECT 1 FROM questions WHERE doc_id = ?", [doc_id]) is not None


# ── The gate: a CH document takes no queries, from either credential ──────────────────

def test_worker_token_question_on_ch_is_rejected(seed_data, tmp_path):
    """B0001: a chat worker's Q POST against its own CH document → 400, nothing stored."""
    raw = _worker_token(CHDOC, tmp_path)
    with patch("modules.flow_gate.api.v1.q_tapi_routes.has_permission", return_value=True):
        resp = _client().post(
            f"/api/v1/q/{CHDOC}/questions",
            json={"asker_kind": "ai", "questions": [{"title": "어느 안?", "body": "1번인가 2번인가?"}]},
            headers={"Authorization": f"Bearer {raw}"},
        )
    assert resp.status_code == 400, resp.text
    # The message must send the worker back to the conversation, not just refuse.
    assert "reply turn" in resp.json()["error_message"]
    assert not _container_exists(CHDOC)


def test_human_session_question_on_ch_is_rejected(seed_data):
    """The blind spot is the document, not the asker: a human [+query] on CH is refused too."""
    from modules.flow_gate.auth.jwt_service import create_access_token
    from modules.flow_gate.db.connection import get_store

    get_store()._db.execute("UPDATE users SET is_active = 1 WHERE user_id = ?", [USER])
    jwt, _ = create_access_token(USER, "c", [])
    with patch("modules.flow_gate.api.v1.q_tapi_routes.has_permission", return_value=True):
        resp = _client().post(
            f"/api/v1/q/{CHDOC}/questions",
            json={"asker_kind": "human", "questions": [{"body": "사람 질의?"}]},
            headers={"Authorization": f"Bearer {jwt}"},
        )
    assert resp.status_code == 400, resp.text
    assert not _container_exists(CHDOC)


def test_ch_gate_runs_before_auth_is_bypassed(seed_data):
    """The gate sits behind auth: an unauthenticated CH post still fails as 401, not 400."""
    resp = _client().post(
        f"/api/v1/q/{CHDOC}/questions",
        json={"asker_kind": "ai", "questions": [{"body": "x"}]},
    )
    assert resp.status_code == 401, resp.text


# ── Regression: every other document type is untouched ────────────────────────────────

def test_non_ch_document_still_accepts_worker_question(seed_data, tmp_path):
    """The gate is CH-only — an ordinary artifact still takes a worker query."""
    raw = _worker_token(TRDOC, tmp_path)
    with patch("modules.flow_gate.api.v1.q_tapi_routes.has_permission", return_value=True):
        resp = _client().post(
            f"/api/v1/q/{TRDOC}/questions",
            json={"asker_kind": "ai", "questions": [{"title": "확인", "body": "이대로 진행?"}]},
            headers={"Authorization": f"Bearer {raw}"},
        )
    assert resp.status_code == 200, resp.text
    assert resp.json()["doc_id"] == TRDOC
    assert _container_exists(TRDOC)


def test_worker_question_skips_ch_predecessor_and_falls_back_to_spine(seed_data, tmp_path):
    """Production shape: B + [CH(result, approved), WP(NULL)] stores on B, never CH."""
    from modules.flow_gate.services import q_service

    spine = "chprj-__ALL__-0261-B0010"
    ch_doc = "chprj-__ALL__-0261-CH0011"
    _mk_doc(spine, "B", 10, review_status="wf_in_progress")
    _mk_doc(ch_doc, "CH", 11, review_status="approved")
    _seed_sequence(spine, [("CH", 0, ch_doc), ("WP", 1, None)])

    raw = _worker_token(spine, tmp_path, action_scope="new")
    with patch("modules.flow_gate.api.v1.q_tapi_routes.has_permission", return_value=True):
        resp = _client().post(
            f"/api/v1/q/{spine}/questions",
            json={"asker_kind": "ai", "questions": [{"body": "WP 범위는 어디까지인가?"}]},
            headers={"Authorization": f"Bearer {raw}"},
        )

    assert resp.status_code == 200, resp.text
    assert resp.json()["doc_id"] == spine
    assert _container_exists(spine)
    assert not _container_exists(ch_doc)
    assert q_service.get_qa_detail(ch_doc)["items"] == []


def test_anchor_skips_latest_ch_and_uses_earlier_t(seed_data):
    """[T(result), CH(result, approved), TR(NULL)] anchors on T, not the latest CH."""
    from modules.flow_gate.services import q_service

    spine = "chprj-__ALL__-0261-B0012"
    t_doc = "chprj-__ALL__-0261-T0013"
    ch_doc = "chprj-__ALL__-0261-CH0014"
    _mk_doc(spine, "B", 12, review_status="wf_in_progress")
    _mk_doc(t_doc, "T", 13, review_status="approved")
    _mk_doc(ch_doc, "CH", 14, review_status="approved")
    _seed_sequence(spine, [("T", 0, t_doc), ("CH", 1, ch_doc), ("TR", 2, None)])

    assert q_service.resolve_question_anchor(spine) == t_doc


def test_service_rejects_direct_ch_question(seed_data):
    """The service boundary refuses CH even when a caller bypasses the Q route."""
    from fastapi import HTTPException
    from modules.flow_gate.services import q_service

    with pytest.raises(HTTPException) as exc_info:
        q_service.add_questions(CHDOC, [{"body": "hidden?"}], asker_kind="ai")

    assert exc_info.value.status_code == 400
    assert "no Q container" in str(exc_info.value.detail)
    assert not _container_exists(CHDOC)


def test_next_empty_ch_with_questions_rolls_back_document_and_q(seed_data, tmp_path, monkeypatch):
    """The next-empty route propagates the service 400 and rolls back its CH transaction."""
    from fastapi import HTTPException
    from modules.flow_gate.db import documents as db_documents
    from modules.flow_gate.db import workflow_sequences as db_wfseq
    from modules.flow_gate.documents.routers import documents as routes
    from modules.flow_gate.services import q_service

    spine = "chprj-__ALL__-0261-B0015"
    _mk_doc(spine, "B", 15, review_status="wf_in_progress")
    seq = _seed_sequence(spine, [("CH", 0, None)])
    stored_path = tmp_path / "0016-CH_document.md"
    created_doc_id = f"{GROUP}.0016-CH"

    monkeypatch.setattr(routes.numbering_service, "reserve_document", lambda **_kwargs: "0016-CH")
    monkeypatch.setattr(routes.storage_paths, "document_path", lambda **_kwargs: stored_path)
    monkeypatch.setattr(routes.storage_paths, "to_storage_relative", lambda *_args, **_kwargs: str(stored_path))
    monkeypatch.setattr(routes, "_get_project_branch", lambda _project_id: "main")
    monkeypatch.setattr(routes, "_try_close_parent_on_child_created", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(routes, "_reject_if_group_ai_running", lambda _doc: None)

    with pytest.raises(HTTPException) as exc_info:
        routes.create_next_empty_document(
            routes.NextEmptyDocumentCreate(
                project_id=PROJECT,
                group_id=GROUP,
                prev_doc_id=spine,
                type_code="CH",
                title="Conversation",
                module="__ALL__",
                questions=[{"body": "hidden?"}],
            ),
            current_user={"user_id": USER},
        )

    assert exc_info.value.status_code == 400
    assert db_documents.get_by_id(created_doc_id) is None
    assert q_service.get_qa_detail(created_doc_id)["items"] == []
    assert not stored_path.exists()
    assert db_wfseq.get_effective_head(seq["id"])["result_doc_id"] is None
