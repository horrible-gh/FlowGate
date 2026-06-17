"""T823 — Block NULL doc_review_status on child document creation.

Tests that every non-R document creation path leaves doc_review_status = 'pending_review'
(or 'approved' for the M fast-path) after insertion.

Covers:
  Case 1 — inbox _handle_new() non-M WITHOUT a workflow head → pending_review
  Case 2 — POST /documents/related (non-R type) → pending_review
  Case 3 — generic POST /documents (non-R type) → pending_review
  Case 4 — qa_service.create_answer_doc() (A type) → pending_review
  Case 5 — generic POST /documents (R type) → doc_review_status stays NULL
  Case 6 — inbox _handle_new() M fast-path → approved  (regression guard)
  Case 7 — inbox _handle_new() NR with workflow head → pending_review (T820 regression guard)
"""
from __future__ import annotations

import json
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

_QUERIES: dict[str, str] = {}
if _QUERIES_JSON.exists():
    raw = json.loads(_QUERIES_JSON.read_text(encoding="utf-8"))
    for section, entries in raw.items():
        if isinstance(entries, dict):
            for key, sql in entries.items():
                if isinstance(sql, str):
                    _QUERIES[f"{section}.{key}"] = sql.replace("%s", "?")


# ── In-memory test DB helpers ────────────────────────────────────────────────

class _MockDB:
    def __init__(self, db_path: str):
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")

    def execute(self, sql: str, params=None):
        self._conn.execute(sql, params or [])
        self._conn.commit()

    def fetch_one(self, sql: str, params=None) -> dict | None:
        row = self._conn.execute(sql, params or []).fetchone()
        return dict(row) if row else None

    def fetch_all(self, sql: str, params=None) -> list[dict]:
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

    def fetch_one(self) -> dict | None:
        if self._last_cursor is None:
            return None
        row = self._last_cursor.fetchone()
        return dict(row) if row else None

    def fetch_all(self) -> list[dict]:
        if self._last_cursor is None:
            return []
        return [dict(r) for r in self._last_cursor.fetchall()]


# ── Shared DB fixture (module scope) ────────────────────────────────────────

@pytest.fixture(scope="module")
def t823_db():
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
def t823_store(t823_db):
    mock_db, _ = t823_db
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
def t823_seed(t823_db):
    """Seed: project, user, group, document types, Q doc for qa_service test."""
    from modules.flow_gate.db import documents as db_docs
    from modules.flow_gate.db import groups as db_groups
    from modules.flow_gate.db import projects, users
    from modules.flow_gate.db import workflow_sequences as db_wfseq
    from modules.flow_gate.db.connection import get_store, now_iso

    now = now_iso()
    project_id = "t823prj"
    group_id = "t823prj-__ALL__-0001"
    r_doc_id = f"{group_id}-R0001"

    projects.create({"project_id": project_id, "project_name": "T823 Test"})
    users.create({
        "user_id": "usr_t823",
        "username": "t823user",
        "email": "t823@test.com",
        "password": "hashed",
    })
    store = get_store()
    store._execute(
        "INSERT OR IGNORE INTO roles (role_id, role_name, created_at, updated_at) VALUES (?,?,?,?)",
        ["role_pm", "PM", now, now],
    )
    for perm in ("document.create", "document.read", "document.update", "perm_document_create"):
        store._execute(
            "INSERT OR IGNORE INTO permissions (permission_id, permission_name, created_at) VALUES (?,?,?)",
            [perm, perm, now],
        )
        store._execute(
            "INSERT OR IGNORE INTO role_permissions (role_id, permission_id) VALUES (?,?)",
            ["role_pm", perm],
        )
    store._execute(
        "INSERT OR IGNORE INTO user_project_roles (user_id, project_id, role_id, granted_at) VALUES (?,?,?,?)",
        ["usr_t823", project_id, "role_pm", now],
    )
    db_groups.create({
        "group_id": group_id,
        "project_id": project_id,
        "module": "__ALL__",
        "title": "T823 Group",
    })
    for type_code, type_name in (
        ("R", "Requirement"),
        ("N", "Notice"),
        ("NR", "Notice Result"),
        ("M", "Memo"),
        ("DS", "Design"),
        ("Q", "Question"),
        ("A", "Answer"),
    ):
        store._execute(
            "INSERT OR IGNORE INTO document_types "
            "(project_id, type_code, type_name, series, is_system, is_active, sort_order, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            [None, type_code, type_name, "work", 1, 1, 0, now, now],
        )

    # Q doc (used by Case 4)
    q_doc_id = f"{group_id}-Q0001"
    db_docs.create({
        "doc_id": q_doc_id,
        "project_id": project_id,
        "type_code": "Q",
        "seq": 1,
        "title": "Question 1",
        "group_id": group_id,
        "module": "__ALL__",
        "owner_id": "usr_t823",
        "status": "open",
    })

    # R doc (used by Case 5, Case 6, Case 7)
    db_docs.create({
        "doc_id": r_doc_id,
        "project_id": project_id,
        "type_code": "R",
        "seq": 1,
        "title": "Root R",
        "group_id": group_id,
        "module": "__ALL__",
        "owner_id": "usr_t823",
    })

    # Workflow sequence for Case 7 (NR with head)
    db_wfseq.insert_sequence(r_doc_id)
    seq = db_wfseq.get_sequence_by_doc_id(r_doc_id)
    seq_id = seq["id"]
    # N (already filled) + NR (pending)
    db_wfseq.insert_sequence_item(seq_id, 1, "N", "Notice", "R", 0)
    db_wfseq.insert_sequence_item(seq_id, 2, "NR", "Notice Result", "R", 1)
    n_doc_id = f"{group_id}-N0001"
    db_docs.create({
        "doc_id": n_doc_id,
        "project_id": project_id,
        "type_code": "N",
        "seq": 1,
        "title": "Notice 1",
        "group_id": group_id,
        "module": "__ALL__",
        "owner_id": "usr_t823",
    })
    db_docs.update(n_doc_id, {"doc_review_status": "approved"})
    items = db_wfseq.get_sequence_items(seq_id)
    n_item = next(i for i in items if i["type"] == "N")
    db_wfseq.set_item_result_doc_id(n_item["id"], n_doc_id)

    yield {
        "project_id": project_id,
        "group_id": group_id,
        "r_doc_id": r_doc_id,
        "n_doc_id": n_doc_id,
        "q_doc_id": q_doc_id,
        "seq_id": seq_id,
        "user_id": "usr_t823",
    }


# ── Inbox client fixture ──────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def t823_inbox_client():
    from fastapi import FastAPI
    from starlette.testclient import TestClient
    from modules.flow_gate.api import inbox_routes

    app = FastAPI()
    app.include_router(inbox_routes.router)
    return TestClient(app)


# ── Document router client fixture ────────────────────────────────────────────

@pytest.fixture(scope="module")
def t823_doc_client():
    from fastapi import FastAPI
    from starlette.testclient import TestClient
    from modules.flow_gate.auth.middleware import get_current_user
    from modules.flow_gate.documents.routers.documents import router as doc_router

    app = FastAPI()
    app.include_router(doc_router)

    def _fake_user():
        return {"user_id": "usr_t823", "username": "t823user"}

    app.dependency_overrides[get_current_user] = _fake_user
    return TestClient(app)


# ── Case 1: Inbox non-M WITHOUT head → pending_review ────────────────────────

def test_inbox_standalone_nr_no_head_gets_pending_review(t823_seed, t823_inbox_client, tmp_path):
    """Inbox new NR submitted to a group with NO matching workflow head → pending_review.

    This covers the T823 gap: transition must fire unconditionally for non-M,
    even when get_pending_head_by_group returns None or a mismatched type.
    """
    from modules.flow_gate.db import documents as db_docs
    from modules.flow_gate.db import groups as db_groups
    from modules.flow_gate.db.connection import get_store, now_iso
    from modules.flow_gate.services import token_service

    # Create a fresh group with NO workflow sequence (standalone submission)
    project_id = t823_seed["project_id"]
    grp_id = "t823prj-__ALL__-0010"
    db_groups.create({
        "group_id": grp_id,
        "project_id": project_id,
        "module": "__ALL__",
        "title": "T823 Standalone Group",
    })
    # R doc in group (required for token issuance) but no workflow_sequences row
    r_doc_id = f"{grp_id}-R0001"
    db_docs.create({
        "doc_id": r_doc_id,
        "project_id": project_id,
        "type_code": "R",
        "seq": 1,
        "title": "Standalone R",
        "group_id": grp_id,
        "module": "__ALL__",
        "owner_id": "usr_t823",
    })
    n_doc_id = f"{grp_id}-N0001"
    db_docs.create({
        "doc_id": n_doc_id,
        "project_id": project_id,
        "type_code": "N",
        "seq": 1,
        "title": "Standalone N",
        "group_id": grp_id,
        "module": "__ALL__",
        "owner_id": "usr_t823",
    })

    with patch.object(token_service, "_scratch_dir", return_value=tmp_path / "scratch_standalone"):
        raw_token = token_service.issue(
            project=project_id,
            group_id=grp_id,
            action_scope="new",
            doc_ref=n_doc_id,
            issued_to="usr_t823",
        )["raw_token"]

    with patch(
        "modules.flow_gate.api.inbox_routes.get_storage_root",
        return_value=tmp_path,
    ), patch(
        "modules.flow_gate.api.inbox_routes.document_path",
        return_value=tmp_path / "docs" / "NR_standalone.md",
    ), patch(
        "modules.flow_gate.api.inbox_routes.numbering_service.reserve_document",
        return_value="NR9901",
    ), patch(
        "modules.flow_gate.rbac.permission_service.has_permission",
        return_value=True,
    ):
        resp = t823_inbox_client.post(
            "/api/v1/inbox",
            json={
                "project": project_id,
                "module": "__ALL__",
                "group": "0010",
                "action": "new",
                "target_id": "N0001",
                "doc_type": "NR",
                "content": "# Standalone NR\n\nBody.",
            },
            headers={"Authorization": f"Bearer {raw_token}"},
        )

    assert resp.status_code == 201, resp.text
    doc_id = resp.json()["doc_id"]
    doc = db_docs.get_by_id(doc_id)
    assert doc is not None
    assert doc["doc_review_status"] == "pending_review", (
        f"Expected pending_review for standalone inbox NR, got {doc['doc_review_status']!r}"
    )


# ── Case 2: POST /documents/related → pending_review ─────────────────────────

def test_related_doc_creation_gets_pending_review(t823_seed, t823_doc_client, tmp_path):
    """POST /documents/related creates a DS doc → doc_review_status = pending_review."""
    from modules.flow_gate.db import documents as db_docs

    project_id = t823_seed["project_id"]
    group_id = t823_seed["group_id"]
    r_doc_id = t823_seed["r_doc_id"]

    with patch(
        "modules.flow_gate.documents.routers.documents.storage_paths.document_path",
        return_value=tmp_path / "ds_related.md",
    ), patch(
        "modules.flow_gate.documents.routers.documents.numbering_service.reserve_document",
        return_value="DS9901",
    ), patch(
        "modules.flow_gate.documents.routers.documents.get_storage_root",
        return_value=tmp_path,
    ):
        resp = t823_doc_client.post(
            "/documents/related",
            json={
                "project_id": project_id,
                "type_code": "DS",
                "title": "Design Spec T823",
                "group_id": group_id,
                "target_id": r_doc_id,
                "module": "__ALL__",
            },
        )

    assert resp.status_code == 201, resp.text
    doc_id = resp.json()["doc_id"]
    doc = db_docs.get_by_id(doc_id)
    assert doc is not None
    assert doc["doc_review_status"] == "pending_review", (
        f"Expected pending_review for related DS doc, got {doc['doc_review_status']!r}"
    )


# ── Case 3: Generic POST /documents (non-R) → pending_review ─────────────────

def test_generic_create_document_non_r_gets_pending_review(t823_seed, t823_doc_client, tmp_path):
    """POST /documents (N type) → doc_review_status = pending_review."""
    from modules.flow_gate.db import documents as db_docs

    project_id = t823_seed["project_id"]
    group_id = t823_seed["group_id"]
    doc_id = f"{group_id}-N9901"

    with patch(
        "modules.flow_gate.documents.routers.documents.storage_paths.document_path",
        return_value=tmp_path / "n_generic.md",
    ), patch(
        "modules.flow_gate.documents.routers.documents.get_storage_root",
        return_value=tmp_path,
    ):
        resp = t823_doc_client.post(
            "/documents",
            json={
                "doc_id": doc_id,
                "project_id": project_id,
                "type_code": "N",
                "seq": 9901,
                "title": "Generic N T823",
                "module": "__ALL__",
                "group_id": group_id,
            },
        )

    assert resp.status_code == 201, resp.text
    doc = db_docs.get_by_id(doc_id)
    assert doc is not None
    assert doc["doc_review_status"] == "pending_review", (
        f"Expected pending_review for generic POST N doc, got {doc['doc_review_status']!r}"
    )


# ── Case 4: qa_service.create_answer_doc() → pending_review ──────────────────

def test_qa_service_create_answer_doc_gets_pending_review(t823_seed, tmp_path):
    """qa_service.create_answer_doc() creates A doc → doc_review_status = pending_review."""
    from modules.flow_gate.db import documents as db_docs
    from modules.flow_gate.services import qa_service

    q_doc_id = t823_seed["q_doc_id"]
    q_doc = db_docs.get_by_id(q_doc_id)
    assert q_doc is not None

    with patch(
        "modules.flow_gate.services.qa_service.numbering_service.reserve_document",
        return_value="9901-A",
    ), patch(
        "modules.flow_gate.services.qa_service.document_path",
        return_value=tmp_path / "a_doc_9901.md",
    ):
        a_doc_id, _ = qa_service.create_answer_doc(
            q_doc=q_doc,
            answer_body="# Answer\n\nContent.",
            actor_user_id="usr_t823",
            module="__ALL__",
        )

    doc = db_docs.get_by_id(a_doc_id)
    assert doc is not None
    assert doc["doc_review_status"] == "pending_review", (
        f"Expected pending_review for A doc, got {doc['doc_review_status']!r}"
    )


# ── Case 5: Generic POST /documents (R type) → doc_review_status stays NULL ──

def test_generic_create_document_r_stays_null(t823_seed, t823_doc_client, tmp_path):
    """POST /documents (R type) → doc_review_status stays NULL (correct per PM policy)."""
    from modules.flow_gate.db import documents as db_docs

    project_id = t823_seed["project_id"]
    group_id = t823_seed["group_id"]
    doc_id = f"{group_id}-R9901"

    with patch(
        "modules.flow_gate.documents.routers.documents.storage_paths.document_path",
        return_value=tmp_path / "r_generic.md",
    ), patch(
        "modules.flow_gate.documents.routers.documents.get_storage_root",
        return_value=tmp_path,
    ):
        resp = t823_doc_client.post(
            "/documents",
            json={
                "doc_id": doc_id,
                "project_id": project_id,
                "type_code": "R",
                "seq": 9901,
                "title": "Generic R T823",
                "module": "__ALL__",
                "group_id": group_id,
            },
        )

    assert resp.status_code == 201, resp.text
    doc = db_docs.get_by_id(doc_id)
    assert doc is not None
    assert doc["doc_review_status"] is None, (
        f"Expected NULL doc_review_status for R doc with no workflow, got {doc['doc_review_status']!r}"
    )


# ── Case 6: Inbox M fast-path → approved (regression guard) ──────────────────

def test_inbox_m_fast_path_approved_regression(t823_seed, t823_db, t823_inbox_client, tmp_path):
    """M-type inbox submission → doc_review_status = approved (M fast-path unchanged)."""
    from modules.flow_gate.db import documents as db_docs
    from modules.flow_gate.db import groups as db_groups
    from modules.flow_gate.db import workflow_sequences as db_wfseq
    from modules.flow_gate.services import token_service

    project_id = t823_seed["project_id"]
    m_group_id = "t823prj-__ALL__-0020"
    db_groups.create({
        "group_id": m_group_id,
        "project_id": project_id,
        "module": "__ALL__",
        "title": "T823 M Group",
    })
    m_r_doc = f"{m_group_id}-R0001"
    db_docs.create({
        "doc_id": m_r_doc,
        "project_id": project_id,
        "type_code": "R",
        "seq": 1,
        "title": "M workflow R",
        "group_id": m_group_id,
        "module": "__ALL__",
        "owner_id": "usr_t823",
    })
    db_wfseq.insert_sequence(m_r_doc)
    m_seq = db_wfseq.get_sequence_by_doc_id(m_r_doc)
    db_wfseq.insert_sequence_item(m_seq["id"], 1, "M", "Memo", "R", 0)

    with patch.object(token_service, "_scratch_dir", return_value=tmp_path / "scratch_m823"):
        raw = token_service.issue(
            project=project_id,
            group_id=m_group_id,
            action_scope="new",
            doc_ref=m_r_doc,
            issued_to="usr_t823",
        )["raw_token"]

    with patch(
        "modules.flow_gate.api.inbox_routes.get_storage_root",
        return_value=tmp_path,
    ), patch(
        "modules.flow_gate.api.inbox_routes.document_path",
        return_value=tmp_path / "docs" / "M823_document.md",
    ), patch(
        "modules.flow_gate.api.inbox_routes.numbering_service.reserve_document",
        return_value="M9901",
    ), patch(
        "modules.flow_gate.rbac.permission_service.has_permission",
        return_value=True,
    ):
        resp = t823_inbox_client.post(
            "/api/v1/inbox",
            json={
                "project": project_id,
                "module": "__ALL__",
                "group": "0020",
                "action": "new",
                "target_id": "R0001",
                "doc_type": "M",
                "content": "# Memo\n\nBody.",
            },
            headers={"Authorization": f"Bearer {raw}"},
        )

    assert resp.status_code == 201, resp.text
    doc_id = resp.json()["doc_id"]
    doc = db_docs.get_by_id(doc_id)
    assert doc["doc_review_status"] == "approved", (
        f"Expected approved for M fast-path, got {doc['doc_review_status']!r}"
    )


# ── Case 7: Inbox NR WITH workflow head → pending_review (T820 regression) ───

def test_inbox_nr_with_head_still_pending_review(t823_seed, t823_inbox_client, tmp_path):
    """Inbox new NR with matching workflow head → pending_review (T820 regression guard)."""
    from modules.flow_gate.db import documents as db_docs
    from modules.flow_gate.services import token_service

    seed = t823_seed
    with patch.object(token_service, "_scratch_dir", return_value=tmp_path / "scratch_t820reg"):
        raw = token_service.issue(
            project=seed["project_id"],
            group_id=seed["group_id"],
            action_scope="new",
            doc_ref=seed["n_doc_id"],
            issued_to=seed["user_id"],
        )["raw_token"]

    with patch(
        "modules.flow_gate.api.inbox_routes.get_storage_root",
        return_value=tmp_path,
    ), patch(
        "modules.flow_gate.api.inbox_routes.document_path",
        return_value=tmp_path / "docs" / "NR_head_reg.md",
    ), patch(
        "modules.flow_gate.api.inbox_routes.numbering_service.reserve_document",
        return_value="NR9801",
    ), patch(
        "modules.flow_gate.rbac.permission_service.has_permission",
        return_value=True,
    ):
        resp = t823_inbox_client.post(
            "/api/v1/inbox",
            json={
                "project": seed["project_id"],
                "module": "__ALL__",
                "group": "0001",
                "action": "new",
                "target_id": "N0001",
                "doc_type": "NR",
                "content": "# NR Head Reg\n\nBody.",
            },
            headers={"Authorization": f"Bearer {raw}"},
        )

    assert resp.status_code == 201, resp.text
    doc_id = resp.json()["doc_id"]
    doc = db_docs.get_by_id(doc_id)
    assert doc is not None
    assert doc["doc_review_status"] == "pending_review", (
        f"Expected pending_review for inbox NR with head, got {doc['doc_review_status']!r}"
    )
    from modules.flow_gate.db import workflow_sequences as db_wfseq
    item = db_wfseq.get_item_by_result_doc_id(doc_id)
    assert item is not None
    assert item["type"] == "NR"
