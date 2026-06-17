"""T820 — Inbox Step 7.5 head lookup: real SQL integration (NR150 Gap A).

Exercises get_pending_head_by_group (not mocked) for first worker registration when
workflow_sequence_items.result_doc_id IS NULL.
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


@pytest.fixture(scope="module")
def t820_db():
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
def t820_store(t820_db):
    mock_db, _ = t820_db
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
def t820_seed(t820_db):
    from modules.flow_gate.db import documents as db_docs
    from modules.flow_gate.db import groups as db_groups
    from modules.flow_gate.db import projects, users
    from modules.flow_gate.db import workflow_sequences as db_wfseq
    from modules.flow_gate.db.connection import get_store, now_iso

    mock_db, _ = t820_db
    now = now_iso()
    project_id = "testprj"
    group_id = "testprj-__ALL__-0001"
    r_doc_id = f"{group_id}-R0001"

    projects.create({"project_id": project_id, "project_name": "T820 Test"})
    users.create({
        "user_id": "usr_t820",
        "username": "t820worker",
        "email": "t820@test.com",
        "password": "hashed",
    })
    store = get_store()
    store._execute(
        "INSERT OR IGNORE INTO roles (role_id, role_name, created_at, updated_at) VALUES (?,?,?,?)",
        ["role_worker", "Worker", now, now],
    )
    for perm in ("document.create", "document.read", "document.update"):
        store._execute(
            "INSERT OR IGNORE INTO permissions (permission_id, permission_name, created_at) VALUES (?,?,?)",
            [perm, perm, now],
        )
        store._execute(
            "INSERT OR IGNORE INTO role_permissions (role_id, permission_id) VALUES (?,?)",
            ["role_worker", perm],
        )
    store._execute(
        "INSERT OR IGNORE INTO user_project_roles (user_id, project_id, role_id, granted_at) VALUES (?,?,?,?)",
        ["usr_t820", project_id, "role_worker", now],
    )
    db_groups.create({
        "group_id": group_id,
        "project_id": project_id,
        "module": "__ALL__",
        "title": "T820 Group",
    })
    for type_code, type_name in (
        ("R", "Requirement"),
        ("N", "Notice"),
        ("NR", "Notice Result"),
        ("M", "Memo"),
    ):
        store._execute(
            "INSERT OR IGNORE INTO document_types "
            "(project_id, type_code, type_name, series, is_system, is_active, sort_order, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            [None, type_code, type_name, "work", 1, 1, 0, now, now],
        )

    db_docs.create({
        "doc_id": r_doc_id,
        "project_id": project_id,
        "type_code": "R",
        "seq": 1,
        "title": "Root R",
        "group_id": group_id,
        "module": "__ALL__",
        "owner_id": "usr_t820",
        "doc_review_status": "wf_in_progress",
    })
    n_doc_id = f"{group_id}-N0001"
    db_docs.create({
        "doc_id": n_doc_id,
        "project_id": project_id,
        "type_code": "N",
        "seq": 1,
        "title": "Notice",
        "group_id": group_id,
        "module": "__ALL__",
        "owner_id": "usr_t820",
    })
    db_docs.update(n_doc_id, {"doc_review_status": "approved"})

    db_wfseq.insert_sequence(r_doc_id)
    seq = db_wfseq.get_sequence_by_doc_id(r_doc_id)
    assert seq is not None
    seq_id = seq["id"]
    db_wfseq.insert_sequence_item(seq_id, 1, "N", "Notice", "R", 0)
    db_wfseq.insert_sequence_item(seq_id, 2, "NR", "Notice Result", "R", 1)
    items = db_wfseq.get_sequence_items(seq_id)
    n_item = next(i for i in items if i["type"] == "N")
    db_wfseq.set_item_result_doc_id(n_item["id"], n_doc_id)

    yield {
        "project_id": project_id,
        "group_id": group_id,
        "r_doc_id": r_doc_id,
        "n_doc_id": n_doc_id,
        "seq_id": seq_id,
        "user_id": "usr_t820",
    }


def _issue_new_token(tmp_path, seed: dict, *, doc_type: str = "NR") -> str:
    from modules.flow_gate.services import token_service

    with patch.object(
        token_service,
        "_scratch_dir",
        return_value=tmp_path / "scratch",
    ):
        result = token_service.issue(
            project=seed["project_id"],
            group_id=seed["group_id"],
            action_scope="new",
            doc_ref=seed["n_doc_id"],
            issued_to=seed["user_id"],
        )
    return result["raw_token"]


def _post_inbox_new(client, raw_token: str, seed: dict, *, doc_type: str):
    return client.post(
        "/api/v1/inbox",
        json={
            "project": seed["project_id"],
            "module": "__ALL__",
            "group": "0001",
            "action": "new",
            "target_id": "N0001",
            "doc_type": doc_type,
            "content": f"# T820 {doc_type}\n\nBody.",
        },
        headers={"Authorization": f"Bearer {raw_token}"},
    )


@pytest.fixture
def t820_client():
    from fastapi import FastAPI
    from starlette.testclient import TestClient
    from modules.flow_gate.api import inbox_routes

    app = FastAPI()
    app.include_router(inbox_routes.router)
    return TestClient(app)


def test_get_pending_head_by_group_finds_null_result_slot(t820_seed):
    """Real SQL: pending NR head with result_doc_id IS NULL; in_progress query returns None."""
    from modules.flow_gate.db import workflow_sequences as db_wfseq

    pending = db_wfseq.get_pending_head_by_group(
        t820_seed["group_id"], t820_seed["project_id"]
    )
    in_prog = db_wfseq.get_in_progress_head_by_group(
        t820_seed["group_id"], t820_seed["project_id"]
    )

    assert pending is not None
    assert pending["type"] == "NR"
    assert pending.get("result_doc_id") is None
    assert in_prog is None


def test_inbox_new_nr_first_registration_pending_review(t820_seed, t820_client, tmp_path):
    """POST inbox new (NR) -> child doc_review_status == pending_review (real Step 7.5)."""
    from modules.flow_gate.db import documents as db_docs

    raw = _issue_new_token(tmp_path, t820_seed, doc_type="NR")

    with patch(
        "modules.flow_gate.api.inbox_routes.get_storage_root",
        return_value=tmp_path,
    ), patch(
        "modules.flow_gate.api.inbox_routes.document_path",
        return_value=tmp_path / "docs" / "NR8201_document.md",
    ), patch(
        "modules.flow_gate.api.inbox_routes.numbering_service.reserve_document",
        return_value="NR8201",
    ), patch(
        "modules.flow_gate.rbac.permission_service.has_permission",
        return_value=True,
    ):
        resp = _post_inbox_new(t820_client, raw, t820_seed, doc_type="NR")

    assert resp.status_code == 201, resp.text
    doc_id = resp.json()["doc_id"]
    assert doc_id == f"{t820_seed['group_id']}-NR8201"

    doc = db_docs.get_by_id(doc_id)
    assert doc is not None
    assert doc["doc_review_status"] == "pending_review"

    from modules.flow_gate.db import workflow_sequences as db_wfseq

    item = db_wfseq.get_item_by_result_doc_id(doc_id)
    assert item is not None
    assert item["type"] == "NR"


def test_inbox_new_m_fast_path_approved(t820_seed, t820_db, t820_client, tmp_path):
    """M-type inbox new -> doc_review_status == approved (regression)."""
    from modules.flow_gate.db import documents as db_docs
    from modules.flow_gate.db import groups as db_groups
    from modules.flow_gate.db import workflow_sequences as db_wfseq
    from modules.flow_gate.services import token_service

    project_id = t820_seed["project_id"]
    m_group_id = "testprj-__ALL__-0002"
    db_groups.create({
        "group_id": m_group_id,
        "project_id": project_id,
        "module": "__ALL__",
        "title": "T820 M Group",
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
        "owner_id": t820_seed["user_id"],
        "doc_review_status": "wf_in_progress",
    })
    db_wfseq.insert_sequence(m_r_doc)
    m_seq = db_wfseq.get_sequence_by_doc_id(m_r_doc)
    db_wfseq.insert_sequence_item(m_seq["id"], 1, "M", "Memo", "R", 0)

    with patch.object(token_service, "_scratch_dir", return_value=tmp_path / "scratch_m"):
        raw = token_service.issue(
            project=project_id,
            group_id=m_group_id,
            action_scope="new",
            doc_ref=m_r_doc,
            issued_to=t820_seed["user_id"],
        )["raw_token"]

    with patch(
        "modules.flow_gate.api.inbox_routes.get_storage_root",
        return_value=tmp_path,
    ), patch(
        "modules.flow_gate.api.inbox_routes.document_path",
        return_value=tmp_path / "docs" / "M8201_document.md",
    ), patch(
        "modules.flow_gate.api.inbox_routes.numbering_service.reserve_document",
        return_value="M8201",
    ), patch(
        "modules.flow_gate.rbac.permission_service.has_permission",
        return_value=True,
    ):
        resp = t820_client.post(
            "/api/v1/inbox",
            json={
                "project": project_id,
                "module": "__ALL__",
                "group": "0002",
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
    assert doc["doc_review_status"] == "approved"


def test_inbox_memo_final_slot_does_not_finalize_parent(t820_seed, t820_db, t820_client, tmp_path):
    """NR0003 regression: a memo (M) filling the LAST workflow slot must NOT silently
    finalize the parent R (wf_done). Auto-approval of the memo still happens, but the
    AC (final-approval) gate must remain — even though the predecessor step is still
    pending_review. Before the fix the inbox path set the parent R to 'wf_done' here,
    bypassing the AC gate and the 'all steps approved' guard (documents.py:819-827
    already deferred to AC; the inbox path had diverged).
    """
    from modules.flow_gate.db import documents as db_docs
    from modules.flow_gate.db import groups as db_groups
    from modules.flow_gate.db import workflow_sequences as db_wfseq
    from modules.flow_gate.services import token_service

    project_id = t820_seed["project_id"]
    group_id = "testprj-__ALL__-0003"
    db_groups.create({
        "group_id": group_id,
        "project_id": project_id,
        "module": "__ALL__",
        "title": "NR0003 memo-final regression",
    })
    r_doc = f"{group_id}-R0001"
    db_docs.create({
        "doc_id": r_doc,
        "project_id": project_id,
        "type_code": "R",
        "seq": 1,
        "title": "Memo-final workflow R",
        "group_id": group_id,
        "module": "__ALL__",
        "owner_id": t820_seed["user_id"],
    })
    # create() does not persist doc_review_status — set the mid-flight state explicitly.
    db_docs.update(r_doc, {"doc_review_status": "wf_in_progress"})
    # Predecessor NR step: produced but still awaiting review (NOT approved).
    pred_doc = f"{group_id}-NR0002"
    db_docs.create({
        "doc_id": pred_doc,
        "project_id": project_id,
        "type_code": "NR",
        "seq": 2,
        "title": "Investigation report (pending review)",
        "group_id": group_id,
        "module": "__ALL__",
        "owner_id": t820_seed["user_id"],
    })
    db_docs.update(pred_doc, {"doc_review_status": "pending_review"})

    # Sequence: slot0 = NR (result already linked), slot1 = M (the empty tail slot).
    db_wfseq.insert_sequence(r_doc)
    seq = db_wfseq.get_sequence_by_doc_id(r_doc)
    db_wfseq.insert_sequence_item(seq["id"], 1, "NR", "Investigation", "R", 0)
    db_wfseq.insert_sequence_item(seq["id"], 2, "M", "Memo", "R", 1)
    items = db_wfseq.get_sequence_items(seq["id"])
    nr_item = next(i for i in items if i["type"] == "NR")
    db_wfseq.set_item_result_doc_id(nr_item["id"], pred_doc)

    # The M slot is now the effective pending head.
    head = db_wfseq.get_pending_head_by_group(group_id, project_id)
    assert head is not None and head["type"] == "M"

    with patch.object(token_service, "_scratch_dir", return_value=tmp_path / "scratch_m_final"):
        raw = token_service.issue(
            project=project_id,
            group_id=group_id,
            action_scope="new",
            doc_ref=r_doc,
            issued_to=t820_seed["user_id"],
        )["raw_token"]

    with patch(
        "modules.flow_gate.api.inbox_routes.get_storage_root",
        return_value=tmp_path,
    ), patch(
        "modules.flow_gate.api.inbox_routes.document_path",
        return_value=tmp_path / "docs" / "M8301_document.md",
    ), patch(
        "modules.flow_gate.api.inbox_routes.numbering_service.reserve_document",
        return_value="M8301",
    ), patch(
        "modules.flow_gate.rbac.permission_service.has_permission",
        return_value=True,
    ):
        resp = t820_client.post(
            "/api/v1/inbox",
            json={
                "project": project_id,
                "module": "__ALL__",
                "group": "0003",
                "action": "new",
                "target_id": "R0001",
                "doc_type": "M",
                "content": "# Closing memo\n\nDone.",
            },
            headers={"Authorization": f"Bearer {raw}"},
        )

    assert resp.status_code == 201, resp.text
    memo_doc_id = resp.json()["doc_id"]

    # Memo is still auto-approved (M needs no review) — unchanged behavior.
    assert db_docs.get_by_id(memo_doc_id)["doc_review_status"] == "approved"

    # ★ Core regression assertion: the parent R must NOT be silently finalized.
    parent = db_docs.get_by_id(r_doc)
    assert parent["doc_review_status"] == "wf_in_progress", (
        f"memo on the final slot silently finalized the workflow: "
        f"R doc_review_status={parent['doc_review_status']!r} (expected 'wf_in_progress')"
    )

    # The still-pending predecessor must remain untouched.
    assert db_docs.get_by_id(pred_doc)["doc_review_status"] == "pending_review"
