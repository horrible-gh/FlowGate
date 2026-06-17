"""Question anchor redirect — B0001 / NR0003 (group 0059) regression.

Bug (B0001): the AI worker registered an ambiguity as a question, but nothing showed up in the console.
Cause (NR0003 §8, approved): the worker token is bound to the workflow spine (the R/B root that owns the
sequence), so when the worker is blocked while producing the report (TR) and registers a question via
POST /q/{doc_id}/questions, that question accumulates on the far-upstream spine (e.g. 0044.0001-R). The user
directs the report from the instruction document (0009-T) and expects the question to appear on 0010-TR
(or 0009-T), not to dig through an already-passed spine.

Fix (subject of this test): a worker-token question redirects its anchor to the 'current work context' document —
  (a) if a head-step produced document exists, that document (the in-progress TR), otherwise
  (b) the preceding instruction document (the T this step reports on),
  falling back to the spine itself if neither exists. A human [+Question] (login session) is not redirected.

Environment: TESTING=1 (temporary SQLite, no sqloader) — mirrors the test_qa_route_auth.py harness.
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


PROJECT = "anchorprj"
GROUP = "anchorprj-__ALL__-0059"
USER = "usr_anchor_001"

# Workflow spine (the B root the worker token is bound to) and its produced docs.
SPINE = "anchorprj-__ALL__-0059-B0001"   # sequence-owning root
TDOC = "anchorprj-__ALL__-0059-T0004"    # work instruction (the step the TR reports on) — approved
TRDOC = "anchorprj-__ALL__-0059-TR0005"  # work report (in-progress report doc), case (a)
# A second spine with no produced predecessor (first-step fallback).
LONE = "anchorprj-__ALL__-0059-B0009"
# A doc that owns no workflow sequence at all.
NOSEQ = "anchorprj-__ALL__-0059-D0007"


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


def _seed_sequence(spine: str, items: list[tuple]):
    """items: list of (type, sort_order, result_doc_id|None)."""
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


@pytest.fixture(scope="module")
def seed_data(tmp_db):
    from modules.flow_gate.db import projects, users, groups

    projects.create({"project_id": PROJECT, "project_name": "Anchor Project"})
    users.create({"user_id": USER, "username": "a", "email": "a@e", "password": "x"})
    for tc in ("B", "T", "TR", "D"):
        _doc_type(tc)
    groups.create({"group_id": GROUP, "project_id": PROJECT, "module": "__ALL__", "title": "G"})

    _mk_doc(SPINE, "B", 1, review_status="wf_in_progress")
    _mk_doc(TDOC, "T", 4, review_status="approved")
    _mk_doc(TRDOC, "TR", 5, review_status="pending_review")
    _mk_doc(LONE, "B", 9, review_status="wf_in_progress")
    _mk_doc(NOSEQ, "D", 7, review_status="pending_review")
    yield


def _edit_token(doc_ref: str, tmp_path) -> str:
    from modules.flow_gate.services import token_service
    with patch.object(token_service, "_scratch_dir", return_value=tmp_path / "scratch"):
        result = token_service.issue(
            project=PROJECT, group_id=GROUP, action_scope="new",
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


# ── resolve_question_anchor unit checks ────────────────────────────────────────────

def test_anchor_b_predecessor_when_report_not_produced(seed_data):
    """(b) TR not produced → redirect to the preceding instruction document (T)."""
    from modules.flow_gate.services import q_service
    _seed_sequence(SPINE, [("T", 0, TDOC), ("TR", 1, None)])
    assert q_service.resolve_question_anchor(SPINE) == TDOC


def test_anchor_a_report_doc_when_in_progress(seed_data):
    """(a) If a produced TR document exists (awaiting review), redirect to that document."""
    from modules.flow_gate.services import q_service
    # 'B0002' spine with the TR step already linked to an in-progress TR doc.
    spine2 = "anchorprj-__ALL__-0059-B0002"
    _mk_doc(spine2, "B", 2, review_status="wf_in_progress")
    _seed_sequence(spine2, [("T", 0, TDOC), ("TR", 1, TRDOC)])
    assert q_service.resolve_question_anchor(spine2) == TRDOC


def test_anchor_first_step_falls_back_to_spine(seed_data):
    """First step (no preceding output) → fall back to the spine itself."""
    from modules.flow_gate.services import q_service
    _seed_sequence(LONE, [("T", 0, None)])
    assert q_service.resolve_question_anchor(LONE) == LONE


def test_anchor_no_sequence_returns_doc(seed_data):
    """Document with no sequence → returned as-is."""
    from modules.flow_gate.services import q_service
    assert q_service.resolve_question_anchor(NOSEQ) == NOSEQ


# ── End-to-end: a worker-token question lands on the work-context document, not the spine ─────────────

def test_worker_question_lands_on_workcontext_not_spine(seed_data, tmp_path):
    """B0001 regression: POST /q/{spine}/questions with a spine-bound worker token →
    the question is stored on the preceding instruction document (T), not the spine."""
    spine3 = "anchorprj-__ALL__-0059-B0003"
    tdoc3 = "anchorprj-__ALL__-0059-T0014"
    _mk_doc(spine3, "B", 3, review_status="wf_in_progress")
    _mk_doc(tdoc3, "T", 14, review_status="approved")
    _seed_sequence(spine3, [("T", 0, tdoc3), ("TR", 1, None)])

    raw = _edit_token(spine3, tmp_path)
    with patch("modules.flow_gate.api.v1.q_tapi_routes.has_permission", return_value=True):
        resp = _client().post(
            f"/api/v1/q/{spine3}/questions",
            json={"asker_kind": "ai", "questions": [{"title": "C 충돌", "body": "어느 코드로?"}]},
            headers={"Authorization": f"Bearer {raw}"},
        )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    # the response doc_id is the redirected work-context document (T) — not the spine.
    assert data["doc_id"] == tdoc3
    assert data["doc_id"] != spine3

    from modules.flow_gate.db.connection import get_store
    # the question container is created on T.
    on_t = get_store()._db.fetch_one(
        "SELECT 1 FROM questions WHERE doc_id = ?", [tdoc3])
    assert on_t is not None
    # no container is created on the spine (far-upstream not used).
    on_spine = get_store()._db.fetch_one(
        "SELECT 1 FROM questions WHERE doc_id = ?", [spine3])
    assert on_spine is None


def test_human_session_question_is_not_redirected(seed_data, tmp_path):
    """A human [+Question] (login session) registers on the clicked document as-is (no redirect)."""
    from modules.flow_gate.auth.jwt_service import create_access_token
    from modules.flow_gate.db.connection import get_store
    spine4 = "anchorprj-__ALL__-0059-B0004"
    tdoc4 = "anchorprj-__ALL__-0059-T0024"
    _mk_doc(spine4, "B", 6, review_status="wf_in_progress")
    _mk_doc(tdoc4, "T", 24, review_status="approved")
    _seed_sequence(spine4, [("T", 0, tdoc4), ("TR", 1, None)])

    get_store()._db.execute("UPDATE users SET is_active = 1 WHERE user_id = ?", [USER])
    jwt, _ = create_access_token(USER, "a", [])
    with patch("modules.flow_gate.api.v1.q_tapi_routes.has_permission", return_value=True):
        resp = _client().post(
            f"/api/v1/q/{spine4}/questions",
            json={"asker_kind": "human", "questions": [{"body": "사람 질의?"}]},
            headers={"Authorization": f"Bearer {jwt}"},
        )
    assert resp.status_code == 200, resp.text
    # the human path keeps the spine as-is (no redirect).
    assert resp.json()["doc_id"] == spine4
