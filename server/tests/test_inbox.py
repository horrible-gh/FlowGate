"""T228: Phase 1 backend unit tests.

Scope:
  1. token_service (issue / verify / consume / revoke, TTL / revoke / consume branches)
  2. validate_doc_path (scratch prefix / UNC / symlink / relative path)
  3. inbound new (doc_path branch, content branch, XOR violation -> 400)
  4. inbound edit (backup creation, CAS conflict -> 409, edit_reason enum validation)
  5. context binding mismatch -> 403
  6. expired token -> 401, revoked token -> 401, consumed token -> 401

Environment: TESTING=1 (temporary SQLite, without sqloader)
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest

# ── Path setup ───────────────────────────────────────────────────────────────

os.environ["TESTING"] = "1"
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
os.environ["FLOWGATE_TOKEN_PEPPER_ACTIVE_ID"] = "test1"
os.environ["FLOWGATE_TOKEN_PEPPER_test1"] = "test-pepper-value-123"

_SERVER_DIR = Path(__file__).resolve().parents[1]
_SCHEMA_DIR = _SERVER_DIR / "sql" / "migrations" / "sqlite"

sys.path.insert(0, str(_SERVER_DIR))


# ── Test DB helper ───────────────────────────────────────────────────────────

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


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def tmp_db():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    mock_db = _MockDB(db_path)
    # Apply all migrations
    for sql_file in sorted(_SCHEMA_DIR.glob("*.sql")):
        try:
            mock_db._conn.executescript(sql_file.read_text(encoding="utf-8"))
        except Exception as e:
            # Some migrations error when adding already-existing tables/columns - ignore
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

    # The active pepper id is set at this module's import (line ~31), but another test
    # module imported later in the same session (e.g. test_r018_impl) overwrites
    # FLOWGATE_TOKEN_PEPPER_ACTIVE_ID at its own import time. token_service.verify hashes
    # with the active pepper, so re-assert it at run time and restore on teardown.
    _orig_active_id = os.environ.get("FLOWGATE_TOKEN_PEPPER_ACTIVE_ID")
    os.environ["FLOWGATE_TOKEN_PEPPER_ACTIVE_ID"] = "test1"

    class _PatchedStore(conn_mod.FlowGateStore):
        def __init__(self):
            self._db = mock_db
            self._sq = None

        def _sql(self, key: str) -> str:
            raise NotImplementedError

    conn_mod.STORE = _PatchedStore()
    yield
    conn_mod.STORE = original_store
    if _orig_active_id is None:
        os.environ.pop("FLOWGATE_TOKEN_PEPPER_ACTIVE_ID", None)
    else:
        os.environ["FLOWGATE_TOKEN_PEPPER_ACTIVE_ID"] = _orig_active_id


@pytest.fixture(scope="module")
def tmp_storage(tmp_path_factory):
    """Temporary storage root."""
    d = tmp_path_factory.mktemp("storage")
    return d


@pytest.fixture(scope="module")
def seed_data(tmp_db):
    """Seed test project + user + permissions."""
    from modules.flow_gate.db import projects, users
    from modules.flow_gate.db.connection import get_store, now_iso

    projects.create({"project_id": "testprj", "project_name": "Test Project"})
    users.create({
        "user_id": "usr_test_001",
        "username": "testuser",
        "email": "test@example.com",
        "password": "hashed_pw",
    })

    store = get_store()
    now = now_iso()
    # Seed role + permission + user_project_roles + role_permissions
    store._execute(
        "INSERT OR IGNORE INTO roles (role_id, role_name, created_at, updated_at) VALUES (?,?,?,?)",
        ["role_worker", "Worker", now, now],
    )
    # Set permission_name equal to permission_id -> avoids UNIQUE conflicts with migration 001 entries such as 'document creation'
    store._execute(
        "INSERT OR IGNORE INTO permissions (permission_id, permission_name, created_at) VALUES (?,?,?)",
        ["document.create", "document.create", now],
    )
    store._execute(
        "INSERT OR IGNORE INTO permissions (permission_id, permission_name, created_at) VALUES (?,?,?)",
        ["document.read", "document.read", now],
    )
    store._execute(
        "INSERT OR IGNORE INTO permissions (permission_id, permission_name, created_at) VALUES (?,?,?)",
        ["document.update", "document.update", now],
    )
    store._execute(
        "INSERT OR IGNORE INTO role_permissions (role_id, permission_id) VALUES (?,?)",
        ["role_worker", "document.create"],
    )
    store._execute(
        "INSERT OR IGNORE INTO role_permissions (role_id, permission_id) VALUES (?,?)",
        ["role_worker", "document.read"],
    )
    store._execute(
        "INSERT OR IGNORE INTO role_permissions (role_id, permission_id) VALUES (?,?)",
        ["role_worker", "document.update"],
    )
    store._execute(
        "INSERT OR IGNORE INTO user_project_roles (user_id, project_id, role_id, granted_at) VALUES (?,?,?,?)",
        ["usr_test_001", "testprj", "role_worker", now],
    )
    # Seed group
    from modules.flow_gate.db import groups as db_groups
    db_groups.create({
        "group_id": "testprj-__ALL__-0001",
        "project_id": "testprj",
        "module": "__ALL__",
        "title": "Test Group",
    })
    # Seed document_type
    store._execute(
        "INSERT OR IGNORE INTO document_types "
        "(project_id, type_code, type_name, series, is_system, is_active, sort_order, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        [None, "NR", "New Request", "work", 1, 1, 0, now_iso(), now_iso()],
    )
    store._execute(
        "INSERT OR IGNORE INTO document_types "
        "(project_id, type_code, type_name, series, is_system, is_active, sort_order, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        [None, "R", "Requirement", "general", 1, 1, 0, now_iso(), now_iso()],
    )
    # Seed prev_doc
    from modules.flow_gate.db import documents as db_docs
    db_docs.create({
        "doc_id": "testprj-__ALL__-0001-R0001",
        "project_id": "testprj",
        "type_code": "R",
        "seq": 1,
        "title": "Root Requirement",
        "group_id": "testprj-__ALL__-0001",
        "module": "__ALL__",
        "owner_id": "usr_test_001",
    })
    yield


# ── 1. validate_doc_path ──────────────────────────────────────────────────────

class TestValidateDocPath:
    def test_valid_path(self, tmp_path):
        from modules.flow_gate.api.inbox_routes import validate_doc_path
        scratch = tmp_path / "scratch" / "tok_123"
        scratch.mkdir(parents=True)
        target = scratch / "test.md"
        target.write_text("hello")
        assert validate_doc_path(str(target), str(scratch)) is True

    def test_relative_path_rejected(self, tmp_path):
        from modules.flow_gate.api.inbox_routes import validate_doc_path
        scratch = tmp_path / "scratch"
        scratch.mkdir()
        assert validate_doc_path("relative/path.md", str(scratch)) is False

    def test_outside_scratch_rejected(self, tmp_path):
        from modules.flow_gate.api.inbox_routes import validate_doc_path
        scratch = tmp_path / "scratch"
        scratch.mkdir()
        outside = tmp_path / "outside" / "evil.md"
        outside.parent.mkdir()
        outside.write_text("evil")
        assert validate_doc_path(str(outside), str(scratch)) is False

    def test_unc_path_rejected(self):
        from modules.flow_gate.api.inbox_routes import validate_doc_path
        assert validate_doc_path("\\\\server\\share\\file.md", "C:\\scratch") is False


# ── 2. token_service ──────────────────────────────────────────────────────────

class TestTokenService:
    def test_issue_and_verify(self, seed_data, tmp_path):
        """Issue -> verify succeeds."""
        from modules.flow_gate.services import token_service

        with patch.object(
            token_service,
            "_scratch_dir",
            return_value=tmp_path / "tok_test",
        ):
            result = token_service.issue(
                project="testprj",
                group_id="testprj-__ALL__-0001",
                action_scope="new",
                doc_ref="R0001",
                issued_to="usr_test_001",
            )

        assert "raw_token" in result
        token_rec = token_service.verify(result["raw_token"])
        assert token_rec["action_scope"] == "new"
        assert token_rec["project"] == "testprj"

    def test_verify_invalid_token(self):
        """Nonexistent token -> 401."""
        from modules.flow_gate.services import token_service
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            token_service.verify("invalid_raw_token_xyz")
        assert exc.value.status_code == 401

    def test_consume(self, seed_data, tmp_path):
        """Reuse after consume -> 401."""
        from modules.flow_gate.services import token_service
        from fastapi import HTTPException

        with patch.object(
            token_service,
            "_scratch_dir",
            return_value=tmp_path / "tok_consume_test",
        ):
            result = token_service.issue(
                project="testprj",
                group_id="testprj-__ALL__-0001",
                action_scope="edit",
                doc_ref="R0001",
                issued_to="usr_test_001",
            )

        raw = result["raw_token"]
        token_id = result["token_id"]

        token_service.consume(token_id, "testprj")

        with pytest.raises(HTTPException) as exc:
            token_service.verify(raw)
        assert exc.value.status_code == 401
        assert "already been used" in exc.value.detail

    def test_revoke(self, seed_data, tmp_path):
        """Reuse after revoke -> 401."""
        from modules.flow_gate.services import token_service
        from fastapi import HTTPException

        with patch.object(
            token_service,
            "_scratch_dir",
            return_value=tmp_path / "tok_revoke_test",
        ):
            result = token_service.issue(
                project="testprj",
                group_id=None,
                action_scope="new",
                doc_ref="R0001",
                issued_to="usr_test_001",
            )

        raw = result["raw_token"]
        token_id = result["token_id"]

        token_service.revoke(token_id, reason="test_revoke")

        with pytest.raises(HTTPException) as exc:
            token_service.verify(raw)
        assert exc.value.status_code == 401
        assert "revoked" in exc.value.detail

    def test_expired_token(self, seed_data):
        """Expired token -> 401."""
        from modules.flow_gate.db import tokens as db_tokens
        from modules.flow_gate.services import token_service
        from fastapi import HTTPException

        # Insert expired token manually
        import hashlib
        raw = "expired_raw_token_xxxyyy"
        pepper = os.environ["FLOWGATE_TOKEN_PEPPER_test1"]
        token_hash = hashlib.sha256((raw + pepper).encode()).hexdigest()
        db_tokens.create({
            "token_id": "tok_expired_001",
            "hash": token_hash,
            "pepper_id": "test1",
            "project": "testprj",
            "group_id": None,
            "doc_ref": None,
            "action_scope": "new",
            "issued_to": "usr_test_001",
            "created_at": "2020-01-01T00:00:00+00:00",
            "expires_at": "2020-01-02T00:00:00+00:00",  # past
        })

        with pytest.raises(HTTPException) as exc:
            token_service.verify(raw)
        assert exc.value.status_code == 401
        assert "expired" in exc.value.detail


# ── 3. Inbound new ──────────────────────────────────────────────────────────

class TestInboxNew:
    def _make_token(self, tmp_path) -> tuple[str, str]:
        """Return (raw_token, token_id)."""
        from modules.flow_gate.services import token_service
        with patch.object(
            token_service,
            "_scratch_dir",
            return_value=tmp_path / "scratch",
        ):
            result = token_service.issue(
                project="testprj",
                group_id="testprj-__ALL__-0001",
                action_scope="new",
                doc_ref="testprj-__ALL__-0001-R0001",
                issued_to="usr_test_001",
            )
        return result["raw_token"], result["token_id"]
        """content branch -> successful response."""
        from modules.flow_gate.api import inbox_routes

        raw, _ = self._make_token(tmp_path)

        # Patch storage_root
        with patch(
            "modules.flow_gate.api.inbox_routes.get_storage_root",
            return_value=tmp_path,
        ), patch(
            "modules.flow_gate.api.inbox_routes.document_path",
            return_value=tmp_path / "docs" / "NR0001_document.md",
        ), patch(
            "modules.flow_gate.api.inbox_routes.numbering_service.reserve_document",
            return_value="NR0001",
        ), patch(
            "modules.flow_gate.rbac.permission_service.has_permission",
            return_value=True,
        ):
            import asyncio
            from starlette.testclient import TestClient
            from fastapi import FastAPI

            app = FastAPI()
            app.include_router(inbox_routes.router)
            client = TestClient(app)

            resp = client.post(
                "/api/v1/inbox",
                json={
                    "project": "testprj",
                    "module": "__ALL__",
                    "group": "0001",
                    "action": "new",
                    "target_id": "R0001",
                    "doc_type": "NR",
                    "content": "# Test Document\n\nContent here.",
                },
                headers={"Authorization": f"Bearer {raw}"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert "doc_id" in data

    def test_new_xor_both(self, seed_data, tmp_path):
        """Both doc_path and content -> 400."""
        from modules.flow_gate.api import inbox_routes

        raw, _ = self._make_token(tmp_path)

        with patch(
            "modules.flow_gate.rbac.permission_service.has_permission",
            return_value=True,
        ):
            from starlette.testclient import TestClient
            from fastapi import FastAPI
            app = FastAPI()
            app.include_router(inbox_routes.router)
            client = TestClient(app)

            resp = client.post(
                "/api/v1/inbox",
                json={
                    "project": "testprj",
                    "module": "__ALL__",
                    "group": "0001",
                    "action": "new",
                    "target_id": "R0001",
                    "doc_type": "NR",
                    "doc_path": "/some/path.md",
                    "content": "# Test",
                },
                headers={"Authorization": f"Bearer {raw}"},
            )
        assert resp.status_code == 400

    def test_new_xor_neither(self, seed_data, tmp_path):
        """Neither doc_path nor content -> 400."""
        from modules.flow_gate.api import inbox_routes

        raw, _ = self._make_token(tmp_path)

        with patch(
            "modules.flow_gate.rbac.permission_service.has_permission",
            return_value=True,
        ):
            from starlette.testclient import TestClient
            from fastapi import FastAPI
            app = FastAPI()
            app.include_router(inbox_routes.router)
            client = TestClient(app)

            resp = client.post(
                "/api/v1/inbox",
                json={
                    "project": "testprj",
                    "module": "__ALL__",
                    "group": "0001",
                    "action": "new",
                    "target_id": "R0001",
                    "doc_type": "NR",
                },
                headers={"Authorization": f"Bearer {raw}"},
            )
        assert resp.status_code == 400

    def test_context_binding_mismatch(self, seed_data, tmp_path):
        """Context binding mismatch -> 403."""
        from modules.flow_gate.api import inbox_routes
        from modules.flow_gate.services import token_service

        # Token with a different project
        with patch.object(token_service, "_scratch_dir", return_value=tmp_path / "s2"):
            result = token_service.issue(
                project="testprj",
                group_id=None,
                action_scope="new",
                doc_ref="testprj-__ALL__-0001-NR0003",  # <- mismatch
                issued_to="usr_test_001",
            )
        raw = result["raw_token"]

        with patch(
            "modules.flow_gate.rbac.permission_service.has_permission",
            return_value=True,
        ):
            from starlette.testclient import TestClient
            from fastapi import FastAPI
            app = FastAPI()
            app.include_router(inbox_routes.router)
            client = TestClient(app)

            resp = client.post(
                "/api/v1/inbox",
                json={
                    "project": "testprj",
                    "module": "__ALL__",
                    "group": "0001",
                    "action": "new",
                    "target_id": "R0001",  # <- mismatch with token doc_ref (NR0003)
                    "doc_type": "NR",
                    "content": "# X",
                },
                headers={"Authorization": f"Bearer {raw}"},
            )
        assert resp.status_code == 403

    def test_post_inbox_returns_201(self, seed_data, tmp_path):
        """POST /inbox (action=new) should return HTTP 201 on success (DEF-001)."""
        from modules.flow_gate.api import inbox_routes

        raw, _ = self._make_token(tmp_path)

        with patch(
            "modules.flow_gate.api.inbox_routes.get_storage_root",
            return_value=tmp_path,
        ), patch(
            "modules.flow_gate.api.inbox_routes.document_path",
            return_value=tmp_path / "docs" / "NR8001_document.md",
        ), patch(
            "modules.flow_gate.api.inbox_routes.numbering_service.reserve_document",
            return_value="NR8001",
        ), patch(
            "modules.flow_gate.rbac.permission_service.has_permission",
            return_value=True,
        ):
            from starlette.testclient import TestClient
            from fastapi import FastAPI

            app = FastAPI()
            app.include_router(inbox_routes.router)
            client = TestClient(app)

            resp = client.post(
                "/api/v1/inbox",
                json={
                    "project": "testprj",
                    "module": "__ALL__",
                    "group": "0001",
                    "action": "new",
                    "target_id": "R0001",
                    "doc_type": "NR",
                    "content": "# Test Document\n\nContent here.",
                },
                headers={"Authorization": f"Bearer {raw}"},
            )

        assert resp.status_code == 201
        data = resp.json()
        assert data["ok"] is True

    def test_post_inbox_stores_canonical_doc_id(self, seed_data, tmp_path):
        """document.doc_id created via POST /inbox must use canonical format ({group_id}-{type_code}{seq}) (DEF-002)."""
        from modules.flow_gate.api import inbox_routes
        from modules.flow_gate.db import documents as db_docs

        raw, _ = self._make_token(tmp_path)

        with patch(
            "modules.flow_gate.api.inbox_routes.get_storage_root",
            return_value=tmp_path,
        ), patch(
            "modules.flow_gate.api.inbox_routes.document_path",
            return_value=tmp_path / "docs" / "NR9001_document.md",
        ), patch(
            "modules.flow_gate.api.inbox_routes.numbering_service.reserve_document",
            return_value="NR9001",
        ), patch(
            "modules.flow_gate.rbac.permission_service.has_permission",
            return_value=True,
        ):
            from starlette.testclient import TestClient
            from fastapi import FastAPI

            app = FastAPI()
            app.include_router(inbox_routes.router)
            client = TestClient(app)

            resp = client.post(
                "/api/v1/inbox",
                json={
                    "project": "testprj",
                    "module": "__ALL__",
                    "group": "0001",
                    "action": "new",
                    "target_id": "R0001",
                    "doc_type": "NR",
                    "content": "# Canonical ID Test",
                },
                headers={"Authorization": f"Bearer {raw}"},
            )

        assert resp.status_code == 201
        data = resp.json()
        expected_canonical = "testprj-__ALL__-0001-NR9001"
        assert data["doc_id"] == expected_canonical

        doc = db_docs.get_by_id(expected_canonical)
        assert doc is not None, f"document {expected_canonical!r} is not present in the DB"
        assert doc["doc_id"] == expected_canonical


# ── 4. Inbound edit ─────────────────────────────────────────────────────────

class TestInboxEdit:
    def _make_edit_token(self, tmp_path, doc_id: str) -> str:
        from modules.flow_gate.services import token_service
        with patch.object(token_service, "_scratch_dir", return_value=tmp_path / "s_edit"):
            result = token_service.issue(
                project="testprj",
                group_id="testprj-__ALL__-0001",
                action_scope="edit",
                doc_ref=doc_id,
                issued_to="usr_test_001",
            )
        return result["raw_token"]

    def _create_target_doc(self, doc_id: str, stored_path: Path) -> None:
        from modules.flow_gate.db import documents as db_docs
        stored_path.parent.mkdir(parents=True, exist_ok=True)
        stored_path.write_text("# Original Content")
        db_docs.create({
            "doc_id": doc_id,
            "project_id": "testprj",
            "type_code": "NR",
            "seq": 99,
            "title": doc_id,
            "group_id": "testprj-__ALL__-0001",
            "module": "__ALL__",
            "owner_id": "usr_test_001",
            "file_path": str(stored_path),
            "revision_no": 0,
        })

    def test_edit_content_backup(self, seed_data, tmp_path):
        """edit content branch -> backup created + revision_no increments."""
        from modules.flow_gate.api import inbox_routes

        doc_id = "testprj-__ALL__-0001-NR0001"
        stored = tmp_path / "docs" / f"{doc_id}_document.md"
        self._create_target_doc(doc_id, stored)

        raw = self._make_edit_token(tmp_path, doc_id)

        with patch(
            "modules.flow_gate.rbac.permission_service.has_permission",
            return_value=True,
        ):
            from starlette.testclient import TestClient
            from fastapi import FastAPI
            app = FastAPI()
            app.include_router(inbox_routes.router)
            client = TestClient(app)

            resp = client.post(
                "/api/v1/inbox",
                json={
                    "project": "testprj",
                    "module": "__ALL__",
                    "group": "0001",
                    "action": "edit",
                    "doc_id": doc_id,
                    "edit_reason": "worker_self",
                    "content": "# Updated Content",
                },
                headers={"Authorization": f"Bearer {raw}"},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["revision_no"] == 1
        assert data["previous_revision_path"] is not None

        # Check backup file exists
        backup = Path(data["previous_revision_path"])
        assert backup.exists()

        # Check document DB revision_no
        from modules.flow_gate.db import documents as db_docs
        doc = db_docs.get_by_id(doc_id)
        assert doc["revision_no"] == 1

    def test_edit_rejected_after_group_final_approval(self, seed_data, tmp_path):
        """An edit token cannot modify a child document after the group R is wf_done."""
        from fastapi import FastAPI
        from starlette.testclient import TestClient

        from modules.flow_gate.api import inbox_routes
        from modules.flow_gate.db import documents as db_docs

        doc_id = "testprj-__ALL__-0001-NR0998"
        stored = tmp_path / "docs" / f"{doc_id}_document.md"
        self._create_target_doc(doc_id, stored)
        raw = self._make_edit_token(tmp_path, doc_id)
        r_doc_id = "testprj-__ALL__-0001-R0001"
        db_docs.update(r_doc_id, {"doc_review_status": "wf_done"})

        try:
            app = FastAPI()
            app.include_router(inbox_routes.router)
            with patch(
                "modules.flow_gate.rbac.permission_service.has_permission",
                return_value=True,
            ):
                response = TestClient(app).post(
                    "/api/v1/inbox",
                    json={
                        "project": "testprj",
                        "module": "__ALL__",
                        "group": "0001",
                        "action": "edit",
                        "doc_id": doc_id,
                        "edit_reason": "worker_self",
                        "content": "# Must remain unchanged",
                    },
                    headers={"Authorization": f"Bearer {raw}"},
                )
        finally:
            db_docs.update(r_doc_id, {"doc_review_status": None})

        assert response.status_code == 422
        assert response.json()["error_message"] == (
            "Modification not allowed after final approval."
        )
        assert stored.read_text() == "# Original Content"

    def test_edit_invalid_reason(self, seed_data, tmp_path):
        """Invalid edit_reason enum -> 400."""
        from modules.flow_gate.api import inbox_routes

        raw = self._make_edit_token(tmp_path, "NR_EDIT_001")

        with patch(
            "modules.flow_gate.rbac.permission_service.has_permission",
            return_value=True,
        ):
            from starlette.testclient import TestClient
            from fastapi import FastAPI
            app = FastAPI()
            app.include_router(inbox_routes.router)
            client = TestClient(app)

            resp = client.post(
                "/api/v1/inbox",
                json={
                    "project": "testprj",
                    "module": "__ALL__",
                    "group": "0001",
                    "action": "edit",
                    "doc_id": "NR_EDIT_001",
                    "edit_reason": "invalid_reason_xyz",
                    "content": "# Updated",
                },
                headers={"Authorization": f"Bearer {raw}"},
            )
        assert resp.status_code == 400

    def test_edit_cas_conflict(self, seed_data, tmp_path):
        """CAS conflict -> 409."""
        from modules.flow_gate.api import inbox_routes
        from modules.flow_gate.db.connection import get_store

        doc_id = "testprj-__ALL__-0001-NR0002"
        stored = tmp_path / "docs_cas" / f"{doc_id}_document.md"
        self._create_target_doc(doc_id, stored)

        raw = self._make_edit_token(tmp_path, doc_id)

        # Simulate CAS failure: after UPDATE, set revision_no back to 0 to force 409
        original_execute = get_store()._db.execute

        call_count = {"n": 0}

        def _patched_execute(sql: str, params=None):
            result = original_execute(sql, params)
            if "revision_no = revision_no + 1" in sql:
                # Increment executed, but set it back to 0 to force CAS validation failure
                get_store()._db._conn.execute(
                    "UPDATE documents SET revision_no = 0 WHERE doc_id = ?", [doc_id]
                )
                get_store()._db._conn.commit()
            return result

        with patch(
            "modules.flow_gate.rbac.permission_service.has_permission",
            return_value=True,
        ), patch.object(get_store()._db, "execute", side_effect=_patched_execute):
            from starlette.testclient import TestClient
            from fastapi import FastAPI
            app = FastAPI()
            app.include_router(inbox_routes.router)
            client = TestClient(app)

            resp = client.post(
                "/api/v1/inbox",
                json={
                    "project": "testprj",
                    "module": "__ALL__",
                    "group": "0001",
                    "action": "edit",
                    "doc_id": doc_id,
                    "edit_reason": "worker_self",
                    "content": "# Conflict",
                },
                headers={"Authorization": f"Bearer {raw}"},
            )
        assert resp.status_code == 409


# ── 5. Access another doc_id with a reissued Phase 3 token -> 403 ──────────

class TestPhase3TokenCrossDocAccess:
    """Accessing a different doc_id with an edit token issued after Phase 3 ment_copy returns 403."""

    def test_phase3_followup_token_wrong_doc_id_returns_403(self, seed_data, tmp_path):
        """Using a Phase 3 token (action_scope=edit, doc_ref=PREV_DOC) to edit a different doc_id returns 403."""
        from modules.flow_gate.api import inbox_routes
        from modules.flow_gate.services import token_service

        # Same conditions as Phase 3 issue_followup_token: action_scope=edit, doc_ref=R0001
        with patch.object(token_service, "_scratch_dir", return_value=tmp_path / "s_phase3"):
            result = token_service.issue(
                project="testprj",
                group_id="testprj-__ALL__-0001",
                action_scope="edit",
                doc_ref="testprj-__ALL__-0001-R0001",   # Document bound to the token
                issued_to="usr_test_001",
            )
        raw = result["raw_token"]

        from starlette.testclient import TestClient
        from fastapi import FastAPI
        app = FastAPI()
        app.include_router(inbox_routes.router)
        with patch(
            "modules.flow_gate.rbac.permission_service.has_permission",
            return_value=True,
        ):
            client = TestClient(app)
            resp = client.post(
                "/api/v1/inbox",
                json={
                    "project": "testprj",
                    "module": "__ALL__",
                    "group": "0001",
                    "action": "edit",
                    "doc_id": "testprj-__ALL__-0001-NR0001",  # Different document from the token-bound R0001
                    "edit_reason": "qna_followup",
                    "content": "# Updated",
                },
                headers={"Authorization": f"Bearer {raw}"},
            )
        assert resp.status_code == 403
        assert "binding" in resp.json()["error_message"]


# ── 6. Missing Bearer header ────────────────────────────────────────────────

class TestInboxAuth:
    def test_no_bearer(self):
        """Missing Authorization header -> 401."""
        from modules.flow_gate.api import inbox_routes
        from starlette.testclient import TestClient
        from fastapi import FastAPI
        app = FastAPI()
        app.include_router(inbox_routes.router)
        client = TestClient(app)

        resp = client.post(
            "/api/v1/inbox",
            json={
                "project": "testprj",
                "module": "__ALL__",
                "group": "0001",
                "action": "new",
                "target_id": "R0001",
                "doc_type": "NR",
                "content": "# X",
            },
        )
        assert resp.status_code == 401


# ── 7. Duplicate-body guard (B0106 / NR0003) ────────────────────────────────

class TestInboxDuplicateBodyGuard:
    """The submission layer occasionally POSTs a stale/reused body (correct title,
    wrong body): the investigation found four NR docs in different groups all carrying
    0082's 5 KB report. The gate must reject a substantial body byte-identical to an
    existing document in a *different* group, while leaving short/boilerplate bodies
    (which legitimately repeat) untouched.
    """

    # A body comfortably above _DUP_MIN_CHARS_DEFAULT (1024), like the 5 KB report.
    _BIG_BODY = "# Investigation Report\n\n" + ("Root cause analysis line. " * 120)
    _SHORT_BODY = "조사지시 가 승인되었습니다.\n\nflowgate/flowgate/flowgate"

    def _seed_second_group(self):
        """Create a second group + its root R doc so a cross-group duplicate is possible.

        Idempotent: the module-scoped DB persists across tests in this class.
        """
        from modules.flow_gate.db import groups as db_groups, documents as db_docs
        if db_groups.get_by_id("testprj-__ALL__-0002") is None:
            db_groups.create({
                "group_id": "testprj-__ALL__-0002",
                "project_id": "testprj",
                "module": "__ALL__",
                "title": "Test Group 2",
            })
        if db_docs.get_by_id("testprj-__ALL__-0002-R0001") is None:
            db_docs.create({
                "doc_id": "testprj-__ALL__-0002-R0001",
                "project_id": "testprj",
                "type_code": "R",
                "seq": 1,
                "title": "Root Requirement 2",
                "group_id": "testprj-__ALL__-0002",
                "module": "__ALL__",
                "owner_id": "usr_test_001",
            })

    def _token_for(self, tmp_path, group_id: str, doc_ref: str) -> str:
        from modules.flow_gate.services import token_service
        with patch.object(token_service, "_scratch_dir", return_value=tmp_path / "scratch"):
            result = token_service.issue(
                project="testprj",
                group_id=group_id,
                action_scope="new",
                doc_ref=doc_ref,
                issued_to="usr_test_001",
            )
        return result["raw_token"]

    def _post(self, tmp_path, group: str, doc_ref: str, doc_code: str, content: str):
        from modules.flow_gate.api import inbox_routes
        raw = self._token_for(
            tmp_path, f"testprj-__ALL__-{group}", f"testprj-__ALL__-{group}-{doc_ref}"
        )
        with patch(
            "modules.flow_gate.api.inbox_routes.get_storage_root", return_value=tmp_path,
        ), patch(
            "modules.flow_gate.api.inbox_routes.document_path",
            return_value=tmp_path / "docs" / f"{doc_code}_document.md",
        ), patch(
            "modules.flow_gate.api.inbox_routes.numbering_service.reserve_document",
            return_value=doc_code,
        ), patch(
            "modules.flow_gate.rbac.permission_service.has_permission", return_value=True,
        ):
            from starlette.testclient import TestClient
            from fastapi import FastAPI
            app = FastAPI()
            app.include_router(inbox_routes.router)
            return TestClient(app).post(
                "/api/v1/inbox",
                json={
                    "project": "testprj", "module": "__ALL__", "group": group,
                    "action": "new", "target_id": doc_ref, "doc_type": "NR",
                    "content": content,
                },
                headers={"Authorization": f"Bearer {raw}"},
            )

    def test_cross_group_identical_substantial_body_rejected(self, seed_data, tmp_path):
        """Second group, same big body → 409 naming the original; first one persists its hash."""
        from modules.flow_gate.db import documents as db_docs
        self._seed_second_group()

        first = self._post(tmp_path, "0001", "R0001", "NR8101", self._BIG_BODY)
        assert first.status_code == 201, first.text
        # The fingerprint is persisted into meta so the guard can match it later.
        stored = db_docs.get_by_id(first.json()["doc_id"])
        assert stored["meta"] and "content_sha256" in stored["meta"]

        second = self._post(tmp_path, "0002", "R0001", "NR8201", self._BIG_BODY)
        assert second.status_code == 409, second.text
        assert first.json()["doc_id"] in second.json()["error_message"]

    def test_same_group_identical_body_allowed(self, seed_data, tmp_path):
        """The guard is cross-group only: a duplicate within the same group is not blocked
        here (same-group dedup is a separate concern; the contamination signature is
        cross-group)."""
        a = self._post(tmp_path, "0001", "R0001", "NR8301", self._BIG_BODY + " v-same-group")
        assert a.status_code == 201, a.text
        b = self._post(tmp_path, "0001", "R0001", "NR8302", self._BIG_BODY + " v-same-group")
        assert b.status_code == 201, b.text

    def test_short_boilerplate_body_exempt(self, seed_data, tmp_path):
        """Short approval-stub bodies repeat legitimately across groups → never blocked."""
        self._seed_second_group()
        a = self._post(tmp_path, "0001", "R0001", "NR8401", self._SHORT_BODY)
        assert a.status_code == 201, a.text
        b = self._post(tmp_path, "0002", "R0001", "NR8402", self._SHORT_BODY)
        assert b.status_code == 201, b.text


# ── 8. Duplicate-body guard on the EDIT path (NR0003 → B0107) ───────────────

class TestInboxEditDuplicateBodyGuard:
    """B0106 only defended `new`, so the same cross-group contamination recurred via
    inbox edit (NR0003, group 0107) — most often on CH conversations the worker
    rewrites wholesale each turn. The guard must now also fire on edit, and an edited
    body must (re)persist its fingerprint so a grown body becomes a detectable twin.
    """

    _BIG_BODY = "# Investigation Report\n\n" + ("Root cause analysis line. " * 120)
    _SHORT_BODY = "조사지시 가 승인되었습니다."

    @staticmethod
    def _fp(text: str) -> str:
        import hashlib
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def _seed_source_group(self, body: str) -> str:
        """A doc in a *different* group carrying body + its content_sha256 in meta.

        The guard matches twins by the meta fingerprint, so the source must already
        have it persisted (exactly what Step 7.1 now does for edited docs).
        """
        import json
        from modules.flow_gate.db import groups as db_groups, documents as db_docs
        if db_groups.get_by_id("testprj-__ALL__-0007") is None:
            db_groups.create({
                "group_id": "testprj-__ALL__-0007",
                "project_id": "testprj",
                "module": "__ALL__",
                "title": "Source Group",
            })
        src_id = "testprj-__ALL__-0007-CH0001"
        if db_docs.get_by_id(src_id) is None:
            db_docs.create({
                "doc_id": src_id,
                "project_id": "testprj",
                "type_code": "CH",
                "seq": 1,
                "title": "Source conversation",
                "group_id": "testprj-__ALL__-0007",
                "module": "__ALL__",
                "owner_id": "usr_test_001",
                "meta": json.dumps({"content_sha256": self._fp(body)}),
            })
        return src_id

    def _create_target_doc(self, doc_id: str, stored_path: Path, body: str = "# Original") -> None:
        from modules.flow_gate.db import documents as db_docs
        stored_path.parent.mkdir(parents=True, exist_ok=True)
        stored_path.write_text(body)
        db_docs.create({
            "doc_id": doc_id,
            "project_id": "testprj",
            "type_code": "CH",
            "seq": 99,
            "title": doc_id,
            "group_id": "testprj-__ALL__-0001",
            "module": "__ALL__",
            "owner_id": "usr_test_001",
            "file_path": str(stored_path),
            "revision_no": 0,
        })

    def _edit(self, tmp_path, doc_id: str, content: str):
        from modules.flow_gate.api import inbox_routes
        from modules.flow_gate.services import token_service
        with patch.object(token_service, "_scratch_dir", return_value=tmp_path / "s_edit"):
            raw = token_service.issue(
                project="testprj",
                group_id="testprj-__ALL__-0001",
                action_scope="edit",
                doc_ref=doc_id,
                issued_to="usr_test_001",
            )["raw_token"]
        with patch(
            "modules.flow_gate.rbac.permission_service.has_permission", return_value=True,
        ):
            from starlette.testclient import TestClient
            from fastapi import FastAPI
            app = FastAPI()
            app.include_router(inbox_routes.router)
            return TestClient(app).post(
                "/api/v1/inbox",
                json={
                    "project": "testprj", "module": "__ALL__", "group": "0001",
                    "action": "edit", "doc_id": doc_id, "edit_reason": "worker_self",
                    "content": content,
                },
                headers={"Authorization": f"Bearer {raw}"},
            )

    def test_edit_cross_group_identical_body_rejected(self, seed_data, tmp_path):
        """Editing a doc to a body byte-identical to another group's doc → 409 (the
        exact NR0003 contamination: a CH body overwritten with another group's CH)."""
        src_id = self._seed_source_group(self._BIG_BODY)
        doc_id = "testprj-__ALL__-0001-CH7101"
        self._create_target_doc(doc_id, tmp_path / "docs" / f"{doc_id}_document.md")

        resp = self._edit(tmp_path, doc_id, self._BIG_BODY)
        assert resp.status_code == 409, resp.text
        assert src_id in resp.json()["error_message"]

    def test_edit_persists_fingerprint(self, seed_data, tmp_path):
        """A substantial edited body persists content_sha256 into meta, so a later
        cross-group copy of THIS body is detectable as a twin (NR0003 §2b/§4-2)."""
        import json
        from modules.flow_gate.db import documents as db_docs
        doc_id = "testprj-__ALL__-0001-CH7201"
        self._create_target_doc(doc_id, tmp_path / "docs" / f"{doc_id}_document.md")

        grown = self._BIG_BODY + "\n\nTurn 2 appended content for this conversation."
        resp = self._edit(tmp_path, doc_id, grown)
        assert resp.status_code == 200, resp.text

        stored = db_docs.get_by_id(doc_id)
        assert stored["meta"] and json.loads(stored["meta"]).get("content_sha256") == self._fp(grown)

    def test_edit_same_group_identical_body_allowed(self, seed_data, tmp_path):
        """The guard is cross-group only: editing to a body identical to a doc in the
        SAME group is not blocked (the contamination signature is cross-group)."""
        from modules.flow_gate.db import documents as db_docs
        # A body unique to this test, so the only twin lives in the SAME group (the
        # shared module DB keeps the group-0007 source from the cross-group test).
        body = self._BIG_BODY + " v-same-group-only"
        sibling = "testprj-__ALL__-0001-CH7301"
        if db_docs.get_by_id(sibling) is None:
            db_docs.create({
                "doc_id": sibling, "project_id": "testprj", "type_code": "CH", "seq": 50,
                "title": sibling, "group_id": "testprj-__ALL__-0001", "module": "__ALL__",
                "owner_id": "usr_test_001",
                "meta": json.dumps({"content_sha256": self._fp(body)}),
            })

        doc_id = "testprj-__ALL__-0001-CH7302"
        self._create_target_doc(doc_id, tmp_path / "docs" / f"{doc_id}_document.md")
        resp = self._edit(tmp_path, doc_id, body)
        assert resp.status_code == 200, resp.text

    def test_edit_short_body_exempt_and_clears_stale_fingerprint(self, seed_data, tmp_path):
        """A short body trips no guard; it also drops any prior fingerprint so the guard
        never matches an outdated body (Step 7.1 else-branch)."""
        import json
        from modules.flow_gate.db import documents as db_docs
        doc_id = "testprj-__ALL__-0001-CH7401"
        self._create_target_doc(doc_id, tmp_path / "docs" / f"{doc_id}_document.md")
        # Seed a prior big-body fingerprint, then shrink the body via edit.
        db_docs.update(doc_id, {"meta": json.dumps({"content_sha256": self._fp(self._BIG_BODY)})})

        resp = self._edit(tmp_path, doc_id, self._SHORT_BODY)
        assert resp.status_code == 200, resp.text
        stored = db_docs.get_by_id(doc_id)
        meta = json.loads(stored["meta"]) if stored["meta"] else {}
        assert "content_sha256" not in meta

