"""0301 T0004 (B0001) — inbox edit must re-point documents.file_path to where it wrote.

Reproduction (NR0003 §3-A): after a time-machine rollback / stale-or-empty file_path,
the AI re-does the work through inbox ``action: edit``. Step 6b writes the new body to a
*recomputed* canonical path when the stored file_path cannot be resolved, but the Step 7
CAS update historically bumped only revision_no/updated_at — it never wrote file_path back.
So documents.file_path kept pointing at the old, unresolvable value and the content reader
(GET /documents/{id}/content -> _document_file_path) 404'd with "Document file not found",
surfaced to the user as "MD 파일이 없다". The AI could rewrite the body forever and the
symptom never cleared — matching B0001's "이거 예전에 수정했던거같은데...".

The fix folds ``file_path = ?`` into the Step 7 CAS update, mirroring _handle_new which
persists file_path on creation. This test drives a real inbox edit against a doc whose
file_path is the empty-column symptom and asserts the pointer now resolves to the freshly
written body (i.e. the content reader would return 200).
"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

# ── Environment / path setup (mirrors test_inbox.py) ────────────────────────────
os.environ["TESTING"] = "1"
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
os.environ["FLOWGATE_TOKEN_PEPPER_ACTIVE_ID"] = "test1"
os.environ["FLOWGATE_TOKEN_PEPPER_test1"] = "test-pepper-value-123"

_SERVER_DIR = Path(__file__).resolve().parents[1]
_SCHEMA_DIR = _SERVER_DIR / "sql" / "migrations" / "sqlite"
sys.path.insert(0, str(_SERVER_DIR))

PROJECT = "testprj"
MODULE = "__ALL__"
GROUP_ID = "testprj-__ALL__-0001"
DOC_ID = "testprj-__ALL__-0001-NR0301"


@pytest.fixture(scope="module")
def _db():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    for sql_file in sorted(_SCHEMA_DIR.glob("*.sql")):
        try:
            conn.executescript(sql_file.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 — some migrations re-add existing objects
            pass
    conn.commit()
    yield conn
    conn.close()
    os.unlink(db_path)


@pytest.fixture(scope="module", autouse=True)
def _patch_store(_db):
    """Point the FlowGate store at the temp SQLite DB (mirrors test_inbox.patch_store)."""
    from modules.flow_gate.db import connection as conn_mod

    class _MockDB:
        def __init__(self, c):
            self._conn = c

        def execute(self, sql, params=None):
            self._conn.execute(sql, params or [])
            self._conn.commit()

        def fetch_one(self, sql, params=None):
            row = self._conn.execute(sql, params or []).fetchone()
            return dict(row) if row else None

        def fetch_all(self, sql, params=None):
            return [dict(r) for r in self._conn.execute(sql, params or []).fetchall()]

    class _PatchedStore(conn_mod.FlowGateStore):
        def __init__(self):
            self._db = _MockDB(_db)
            self._sq = None

        def _sql(self, key):  # pragma: no cover - unused in this test
            raise NotImplementedError

    original = conn_mod.STORE
    os.environ["FLOWGATE_TOKEN_PEPPER_ACTIVE_ID"] = "test1"
    conn_mod.STORE = _PatchedStore()
    yield
    conn_mod.STORE = original


@pytest.fixture(scope="module", autouse=True)
def _seed(_patch_store):
    from modules.flow_gate.db import projects, users, groups as db_groups
    from modules.flow_gate.db.connection import get_store, now_iso

    projects.create({"project_id": PROJECT, "project_name": "Test Project"})
    users.create({
        "user_id": "usr_test_001",
        "username": "testuser",
        "email": "test@example.com",
        "password": "hashed_pw",
    })
    store = get_store()
    now = now_iso()
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
        ["usr_test_001", PROJECT, "role_worker", now],
    )
    db_groups.create({
        "group_id": GROUP_ID,
        "project_id": PROJECT,
        "module": MODULE,
        "title": "Test Group",
    })
    store._execute(
        "INSERT OR IGNORE INTO document_types "
        "(project_id, type_code, type_name, series, is_system, is_active, sort_order, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        [None, "NR", "New Request", "work", 1, 1, 0, now, now],
    )
    yield


def test_edit_repoints_file_path_for_empty_column(tmp_path):
    """A doc whose file_path is empty (the missing-file symptom) is edited: after the
    edit, file_path resolves to the freshly written body — the reader would return 200.

    Auth (token verify/consume) is mocked at the module boundary — the tokens table and
    Step 3 binding are not under test here, only the Step 6b/7 file_path re-point. This
    also sidesteps a pre-existing, out-of-scope local-migration defect (a duplicate-063
    numbering makes migration 064's tokens rebuild drop continuation_instruction_mode).
    """
    from fastapi import FastAPI
    from starlette.testclient import TestClient

    from modules.flow_gate.api import inbox_routes
    from modules.flow_gate.services import token_service
    from modules.flow_gate.db import documents as db_docs
    from modules.flow_gate.storage import paths as storage_paths

    # Target doc with the exact B0001 symptom: a DB row whose file_path column is empty,
    # so no on-disk .md can be located from it.
    db_docs.create({
        "doc_id": DOC_ID,
        "project_id": PROJECT,
        "type_code": "NR",
        "seq": 301,
        "title": DOC_ID,
        "group_id": GROUP_ID,
        "module": MODULE,
        "branch": "main",
        "owner_id": "usr_test_001",
        "file_path": "",          # missing-file symptom — nothing resolves from here
        "revision_no": 0,
    })
    assert (db_docs.get_by_id(DOC_ID) or {}).get("file_path") in ("", None)

    scratch = tmp_path / "scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    token_rec = {
        "token_id": "tok_test_0301",
        "project": PROJECT,
        "action_scope": "edit",
        "doc_ref": DOC_ID,
        "issued_to": "usr_test_001",
        "scratch_dir": str(scratch),
    }
    new_body = "# Re-done after time-machine rollback\n\nfresh body\n"

    with patch(
        "modules.flow_gate.rbac.permission_service.has_permission",
        return_value=True,
    ), patch.object(storage_paths, "get_storage_root", return_value=tmp_path), \
            patch.object(token_service, "verify", return_value=token_rec), \
            patch.object(token_service, "consume", return_value=None):
        app = FastAPI()
        app.include_router(inbox_routes.router)
        resp = TestClient(app).post(
            "/api/v1/inbox",
            json={
                "project": PROJECT,
                "module": MODULE,
                "group": "0001",
                "action": "edit",
                "doc_id": DOC_ID,
                "edit_reason": "worker_self",
                "content": new_body,
            },
            headers={"Authorization": "Bearer dummy-token"},
        )

        assert resp.status_code == 200, resp.text
        assert resp.json()["revision_no"] == 1

        # The fix: file_path is now persisted, non-empty, and stored relative (B0001 guard).
        doc = db_docs.get_by_id(DOC_ID)
        stored = doc["file_path"]
        assert stored, "file_path must be re-pointed to the written location, not left empty"
        assert not stored.startswith("/") and ":" not in stored, (
            f"file_path must be storage-relative, got {stored!r}"
        )

        # And it must resolve to the freshly written body — i.e. the content reader
        # (_document_file_path -> resolve_storage_path) would now return 200, not 404.
        resolved = storage_paths.resolve_storage_path(stored, PROJECT, branch="main")
        assert resolved is not None and resolved.is_file(), (
            f"reader cannot resolve re-pointed file_path {stored!r}"
        )
        assert resolved.read_text(encoding="utf-8") == new_body
