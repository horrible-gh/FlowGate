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

    class _PatchedStore(conn_mod.FlowGateStore):
        def __init__(self):
            self._db = mock_db
            self._sq = None

        def _sql(self, key: str) -> str:
            raise NotImplementedError

    conn_mod.STORE = _PatchedStore()
    yield
    conn_mod.STORE = original_store


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
        assert "already used" in exc.value.detail

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

