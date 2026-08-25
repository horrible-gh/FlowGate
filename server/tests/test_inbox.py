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

    def test_tr_scope_gate_runs_for_ts_new_submission(self, seed_data, tmp_path):
        """0390 R0001/TR0005: TS is now a MUTATING_STEP_TYPES member, so a new TS
        submission must go through the same Step 5.7 작업범위 검증 gate as TR — a
        rejecting verdict must 422 the submission before any doc_id is reserved."""
        from modules.flow_gate.api import inbox_routes
        from modules.flow_gate.db.connection import get_store, now_iso

        store = get_store()
        store._execute(
            "INSERT OR IGNORE INTO document_types "
            "(project_id, type_code, type_name, series, is_system, is_active, sort_order, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            [None, "TS", "Test Scenario", "work", 1, 1, 0, now_iso(), now_iso()],
        )

        raw, _ = self._make_token(tmp_path)

        reject_verdict = {
            "verdict": "reject",
            "stage": "enforce",
            "codes": ["TRV-003"],
            "notice": "TR 작업범위 검증 반려 (test)",
            "out_of_scope": [],
            "unconfirmed": ["some/other/file.py"],
            "unreported": [],
            "branch": "main",
        }

        with patch(
            "modules.flow_gate.rbac.permission_service.has_permission",
            return_value=True,
        ), patch(
            "modules.flow_gate.api.inbox_routes.tr_scope_service.evaluate",
            return_value=reject_verdict,
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
                    "doc_type": "TS",
                    "content": "# TS Doc\n\n## 테스트 케이스\n### TC-1: x\n- cmd: echo x\n",
                },
                headers={"Authorization": f"Bearer {raw}"},
            )

        assert resp.status_code == 422
        assert "doc_id" not in resp.json()

    def test_tr_scope_gate_skipped_for_non_mutating_new_submission(self, seed_data, tmp_path):
        """Non-mutating types (e.g. NR) must not even invoke tr_scope_service.evaluate —
        the MUTATING_STEP_TYPES membership check must short-circuit before the call,
        so a broadened gate can never start rejecting requirement/notice submissions."""
        from modules.flow_gate.api import inbox_routes
        from unittest.mock import MagicMock

        raw, _ = self._make_token(tmp_path)
        never_called = MagicMock(side_effect=AssertionError(
            "tr_scope_service.evaluate must not be called for a non-mutating doc_type"
        ))

        with patch(
            "modules.flow_gate.api.inbox_routes.get_storage_root",
            return_value=tmp_path,
        ), patch(
            "modules.flow_gate.api.inbox_routes.document_path",
            return_value=tmp_path / "docs" / "NR7001_document.md",
        ), patch(
            "modules.flow_gate.api.inbox_routes.numbering_service.reserve_document",
            return_value="NR7001",
        ), patch(
            "modules.flow_gate.rbac.permission_service.has_permission",
            return_value=True,
        ), patch(
            "modules.flow_gate.api.inbox_routes.tr_scope_service.evaluate",
            new=never_called,
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
                    "content": "# NR Doc\n\nNo scope section at all.",
                },
                headers={"Authorization": f"Bearer {raw}"},
            )

        assert resp.status_code == 201
        assert never_called.call_count == 0


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

    def test_tr_scope_gate_runs_for_ts_edit_resubmission(self, seed_data, tmp_path):
        """0390 R0001/TR0005: a rejected TS document's edit/resubmission path
        (_handle_edit Step 5.7) must also run the 작업범위 검증 gate — this is the
        bypass TR0005 §2 point 2 called out (B0106-style hole if only _handle_new
        were widened)."""
        from modules.flow_gate.api import inbox_routes
        from modules.flow_gate.db import documents as db_docs

        doc_id = "testprj-__ALL__-0001-TS0001"
        stored = tmp_path / "docs_ts" / f"{doc_id}_document.md"
        stored.parent.mkdir(parents=True, exist_ok=True)
        stored.write_text("# Original TS Content")
        db_docs.create({
            "doc_id": doc_id,
            "project_id": "testprj",
            "type_code": "TS",
            "seq": 1,
            "title": doc_id,
            "group_id": "testprj-__ALL__-0001",
            "module": "__ALL__",
            "owner_id": "usr_test_001",
            "file_path": str(stored),
            "revision_no": 0,
        })

        raw = self._make_edit_token(tmp_path, doc_id)

        reject_verdict = {
            "verdict": "reject",
            "stage": "enforce",
            "codes": ["TRV-003"],
            "notice": "TR 작업범위 검증 반려 (test)",
            "out_of_scope": [],
            "unconfirmed": ["some/other/file.py"],
            "unreported": [],
            "branch": "main",
        }

        with patch(
            "modules.flow_gate.rbac.permission_service.has_permission",
            return_value=True,
        ), patch(
            "modules.flow_gate.api.inbox_routes.tr_scope_service.evaluate",
            return_value=reject_verdict,
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
                    "content": "# Revised TS Content\n\n## 테스트 케이스\n### TC-1: x\n- cmd: echo x\n",
                },
                headers={"Authorization": f"Bearer {raw}"},
            )

        assert resp.status_code == 422
        # Rejection must not have written the edit through.
        assert stored.read_text() == "# Original TS Content"
        assert db_docs.get_by_id(doc_id)["revision_no"] == 0


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

    def test_new_persists_normalized_fingerprint(self, seed_data, tmp_path):
        """A substantial new body persists content_sha256_norm alongside the exact hash,
        so a whitespace-only near-duplicate becomes detectable (NR0003 §5.1c)."""
        import json
        from modules.flow_gate.db import documents as db_docs
        body = self._BIG_BODY + " v-norm-persist"
        resp = self._post(tmp_path, "0001", "R0001", "NR8501", body)
        assert resp.status_code == 201, resp.text
        meta = json.loads(db_docs.get_by_id(resp.json()["doc_id"])["meta"])
        assert "content_sha256" in meta and "content_sha256_norm" in meta

    def test_cross_group_whitespace_near_duplicate_rejected(self, seed_data, tmp_path):
        """A clone that differs only by reflow/indentation evades the byte-exact hash but
        not the normalized one → 409 naming the original (NR0003 §5.1c)."""
        self._seed_second_group()
        body = self._BIG_BODY + " v-near-dup"
        first = self._post(tmp_path, "0001", "R0001", "NR8601", body)
        assert first.status_code == 201, first.text

        # Reflowed clone: collapse spaces to newlines + add trailing whitespace. The
        # exact sha differs (no byte match) but the whitespace-normalized form is equal.
        reflowed = body.replace(" ", "\n  ") + "\n\n"
        assert reflowed != body
        second = self._post(tmp_path, "0002", "R0001", "NR8602", reflowed)
        assert second.status_code == 409, second.text
        assert first.json()["doc_id"] in second.json()["error_message"]


# ── 8. Duplicate-body guard on the EDIT path (NR0003 → B0107) ───────────────

class TestInboxEditDuplicateBodyGuard:
    """B0106 only defended `new`, so the same cross-group contamination recurred via
    inbox edit (NR0003, group 0107) — most often on CH conversations the worker
    rewrites wholesale each turn. The guard must now also fire on edit, and an edited
    body must (re)persist its fingerprint so a grown body becomes a detectable twin.

    0432 T0005 §2-5: every CH document below is LEGACY — ``_create_target_doc`` writes a
    ``documents`` row and nothing else, so ``conversation_docs`` has no row and
    ``migration_state`` is ``pending``. That is why the 200 expectations here survive the
    migrated-conversation full-body block added in 0432 (0344.0005-L §2-16 explicitly does
    not fire on LEGACY: the file is still the record of truth). The assertion inside
    ``test_edit_accepts_matching_frontmatter_identity`` pins that premise, so adding a
    ``conversation_docs`` row to this fixture breaks there rather than silently turning
    these cases into a different test.
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

    def _create_target_doc(
        self,
        doc_id: str,
        stored_path: Path,
        body: str = "# Original",
        target_id: str | None = None,
        type_code: str = "CH",
    ) -> None:
        from modules.flow_gate.db import documents as db_docs
        stored_path.parent.mkdir(parents=True, exist_ok=True)
        stored_path.write_text(body)
        db_docs.create({
            "doc_id": doc_id,
            "project_id": "testprj",
            "type_code": type_code,
            "seq": 99,
            "title": doc_id,
            "group_id": "testprj-__ALL__-0001",
            "module": "__ALL__",
            "owner_id": "usr_test_001",
            "file_path": str(stored_path),
            "revision_no": 0,
            "target_id": target_id,
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

    def test_new_rejects_foreign_frontmatter_identity_even_for_short_body(self, seed_data, tmp_path):
        """A copied prefix snapshot may be too short for content_sha256 matching, but its
        YAML frontmatter still declares the source document identity and must be rejected."""
        from modules.flow_gate.db import documents as db_docs

        foreign_prefix = (
            "---\n"
            "project: mailanchor\n"
            "module: ui\n"
            "group_id: mailanchor.ui.0001\n"
            "doc_number: 0004-CH\n"
            "type: CH\n"
            "target_id: mailanchor.ui.0001.0003-CH\n"
            "title: Source conversation\n"
            "---\n"
            "short copied prefix\n"
        )

        resp = TestInboxDuplicateBodyGuard()._post(
            tmp_path, "0001", "R0001", "NR7501", foreign_prefix
        )
        assert resp.status_code == 409, resp.text
        assert "Frontmatter identity mismatch" in resp.json()["error_message"]
        assert db_docs.get_by_id("testprj-__ALL__-0001-NR7501") is None

    def test_edit_rejects_foreign_frontmatter_identity_even_without_fingerprint_match(
        self, seed_data, tmp_path
    ):
        """Regression for group 0128: edit must reject a CH overwrite whose submitted
        body declares another conversation, even when no source content_sha256 can match."""
        doc_id = "testprj-__ALL__-0001-CH7502"
        stored_path = tmp_path / "docs" / f"{doc_id}_document.md"
        self._create_target_doc(
            doc_id,
            stored_path,
            body="# Original conversation",
            target_id="testprj-__ALL__-0001-R0001",
        )
        foreign_prefix = (
            "---\n"
            "project: mailanchor\n"
            "module: ui\n"
            "group_id: mailanchor.ui.0001\n"
            "doc_number: 0004-CH\n"
            "type: CH\n"
            "target_id: mailanchor.ui.0001.0003-CH\n"
            "title: Source conversation\n"
            "---\n"
            "short copied prefix\n"
        )

        resp = self._edit(tmp_path, doc_id, foreign_prefix)
        assert resp.status_code == 409, resp.text
        assert "Frontmatter identity mismatch" in resp.json()["error_message"]
        assert stored_path.read_text() == "# Original conversation"

    def test_edit_accepts_matching_frontmatter_identity(self, seed_data, tmp_path):
        """A matching frontmatter identity is allowed; the guard only blocks conflicts."""
        doc_id = "testprj-__ALL__-0001-CH7503"
        self._create_target_doc(
            doc_id,
            tmp_path / "docs" / f"{doc_id}_document.md",
            target_id="testprj-__ALL__-0001-R0001",
        )
        own_body = (
            "---\n"
            "project: testprj\n"
            "module: __ALL__\n"
            "group_id: testprj-__ALL__-0001\n"
            "doc_number: CH7503\n"
            "type: CH\n"
            "target_id: testprj-__ALL__-0001-R0001\n"
            "title: Target conversation\n"
            "---\n"
            "own short body\n"
        )

        resp = self._edit(tmp_path, doc_id, own_body)
        assert resp.status_code == 200, resp.text
        # 0432 T0005 §3-5: 이 200 은 "대화라서"가 아니라 "아직 이관되지 않은 대화라서"다.
        # 픽스처에 conversation_docs 행이 생기면 이 문서는 migrated 가 되고 위 200 은
        # 409 로 바뀐다 — 그 변화를 조용히 넘기지 않도록 전제를 여기서 못박는다.
        from modules.flow_gate.db import conversation_turns
        assert conversation_turns.migration_state(doc_id) != "migrated"

    # ── T0004 2.1/2.2/2.3: submission-header normalization + malformed-frontmatter guard ──

    def _post_new(
        self, tmp_path, group: str, doc_ref: str, doc_code: str,
        content: str | None = None, doc_path: str | None = None, dry_run: bool = False,
        doc_type: str = "NR",
    ):
        from modules.flow_gate.api import inbox_routes
        from modules.flow_gate.services import token_service
        with patch.object(token_service, "_scratch_dir", return_value=tmp_path / "scratch"):
            raw = token_service.issue(
                project="testprj",
                group_id=f"testprj-__ALL__-{group}",
                action_scope="new",
                doc_ref=f"testprj-__ALL__-{group}-{doc_ref}",
                issued_to="usr_test_001",
            )["raw_token"]
        payload = {
            "project": "testprj", "module": "__ALL__", "group": group,
            "action": "new", "target_id": doc_ref, "doc_type": doc_type,
        }
        if content is not None:
            payload["content"] = content
        if doc_path is not None:
            payload["doc_path"] = doc_path
        if dry_run:
            payload["dry_run"] = True
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
                "/api/v1/inbox", json=payload,
                headers={"Authorization": f"Bearer {raw}"},
            )

    _COLLAPSED_HEADER = (
        "next_type: NR next_type_detail: 조사레포트 project: testprj module: __ALL__ "
        "group: 0001 title: Collapsed header case target_id: R0001\n\nBody text."
    )
    _COLLAPSED_HEADER_NORMALIZED = (
        "next_type: NR\nnext_type_detail: 조사레포트\nproject: testprj\n"
        "module: __ALL__\ngroup: 0001\ntitle: Collapsed header case\n"
        "target_id: R0001\n\nBody text."
    )
    _COLLAPSED_HEADER_NORMALIZATIONS = [
        {"kind": "collapsed_next_header", "line_start": 1, "inserted_breaks": 6}
    ]

    def test_new_normalizes_collapsed_header_and_saves_repaired_body(self, seed_data, tmp_path):
        """R0001's cited collapsed one-liner is repaired to 7 LF lines before any
        guard runs, the repair is reported in the response, and the *saved* file
        carries the repaired text - not the original collapsed bytes (T0004 2.1/2.2)."""
        import hashlib
        resp = self._post_new(tmp_path, "0001", "R0001", "NR7601", content=self._COLLAPSED_HEADER)
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert data["normalizations"] == self._COLLAPSED_HEADER_NORMALIZATIONS
        saved = Path(data["stored_path"]).read_text(encoding="utf-8")
        assert saved == self._COLLAPSED_HEADER_NORMALIZED
        # change_summary reflects the *saved* (post-normalization) 7-line body,
        # not the collapsed one-liner that was sent (T0004 completion criterion 5).
        after = data["change_summary"]["after"]
        assert after["lines"] == len(self._COLLAPSED_HEADER_NORMALIZED.split("\n"))
        assert after["content_sha256"] == hashlib.sha256(
            self._COLLAPSED_HEADER_NORMALIZED.encode("utf-8")
        ).hexdigest()

    def test_new_dry_run_reports_same_normalization_as_real(self, seed_data, tmp_path):
        dry = self._post_new(
            tmp_path, "0001", "R0001", "NR7602", content=self._COLLAPSED_HEADER, dry_run=True,
        )
        assert dry.status_code == 200, dry.text
        assert dry.json()["would_register"]["normalizations"] == self._COLLAPSED_HEADER_NORMALIZATIONS

        real = self._post_new(tmp_path, "0001", "R0001", "NR7603", content=self._COLLAPSED_HEADER)
        assert real.status_code == 201, real.text
        assert real.json()["normalizations"] == self._COLLAPSED_HEADER_NORMALIZATIONS

    def test_new_already_correct_header_is_not_reported_as_normalized(self, seed_data, tmp_path):
        resp = self._post_new(tmp_path, "0001", "R0001", "NR7604", content=self._COLLAPSED_HEADER_NORMALIZED)
        assert resp.status_code == 201, resp.text
        assert "normalizations" not in resp.json()
        saved = Path(resp.json()["stored_path"]).read_text(encoding="utf-8")
        assert saved == self._COLLAPSED_HEADER_NORMALIZED

    def test_new_doc_path_submission_normalizes_the_same_way(self, seed_data, tmp_path):
        """content and doc_path submissions must be checked and saved identically
        (T0004 2.2: no second, un-normalized code path for file uploads)."""
        scratch = tmp_path / "scratch"
        scratch.mkdir(parents=True, exist_ok=True)
        src = scratch / "submission.md"
        src.write_text(self._COLLAPSED_HEADER, encoding="utf-8")

        resp = self._post_new(tmp_path, "0001", "R0001", "NR7605", doc_path=str(src))
        assert resp.status_code == 201, resp.text
        assert resp.json()["normalizations"] == self._COLLAPSED_HEADER_NORMALIZATIONS
        saved = Path(resp.json()["stored_path"]).read_text(encoding="utf-8")
        assert saved == self._COLLAPSED_HEADER_NORMALIZED

    def test_new_body_fingerprint_is_checked_against_pre_normalization_bytes(
        self, seed_data, tmp_path
    ):
        """T0004 2.2's contract: body_sha256/body_chars is the caller's integrity
        check on what it actually SENT (the collapsed original), computed before
        the server ever normalizes anything. If the guard instead compared against
        the *normalized* (repaired) body, this legitimate request -- whose hashes
        are correct for the bytes it sent -- would spuriously 422."""
        import hashlib
        from modules.flow_gate.api import inbox_routes
        from modules.flow_gate.services import token_service

        raw_bytes = self._COLLAPSED_HEADER.encode("utf-8")
        with patch.object(token_service, "_scratch_dir", return_value=tmp_path / "scratch"):
            raw = token_service.issue(
                project="testprj", group_id="testprj-__ALL__-0001", action_scope="new",
                doc_ref="testprj-__ALL__-0001-R0001", issued_to="usr_test_001",
            )["raw_token"]
        with patch(
            "modules.flow_gate.api.inbox_routes.get_storage_root", return_value=tmp_path,
        ), patch(
            "modules.flow_gate.api.inbox_routes.document_path",
            return_value=tmp_path / "docs" / "NR7611_document.md",
        ), patch(
            "modules.flow_gate.api.inbox_routes.numbering_service.reserve_document",
            return_value="NR7611",
        ), patch(
            "modules.flow_gate.rbac.permission_service.has_permission", return_value=True,
        ):
            from starlette.testclient import TestClient
            from fastapi import FastAPI
            app = FastAPI()
            app.include_router(inbox_routes.router)
            resp = TestClient(app).post(
                "/api/v1/inbox",
                json={
                    "project": "testprj", "module": "__ALL__", "group": "0001",
                    "action": "new", "target_id": "R0001", "doc_type": "NR",
                    "content": self._COLLAPSED_HEADER,
                    "body_sha256": hashlib.sha256(raw_bytes).hexdigest(),
                    "body_chars": len(self._COLLAPSED_HEADER),
                },
                headers={"Authorization": f"Bearer {raw}"},
            )
        assert resp.status_code == 201, resp.text
        saved = Path(resp.json()["stored_path"]).read_text(encoding="utf-8")
        assert saved == self._COLLAPSED_HEADER_NORMALIZED

    def test_new_rejects_ambiguous_frontmatter_even_when_first_key_is_unchecked(
        self, seed_data, tmp_path
    ):
        """NR0003's core finding: a collapsed line whose first key is not one of the
        identity-checked fields (here: title) previously 200'd with every identity
        field silently absent. It must now be rejected as malformed (422), not
        silently accepted (T0004 task 2.3)."""
        malformed = (
            "---\n"
            "title: T next_type_detail: 조사레포트 project: testprj module: __ALL__ "
            "group: 0001 target_id: R0001\n"
            "---\n"
            "short body\n"
        )
        resp = self._post_new(tmp_path, "0001", "R0001", "NR7606", content=malformed)
        assert resp.status_code == 422, resp.text

    def test_new_rejects_unclosed_frontmatter(self, seed_data, tmp_path):
        malformed = "---\nproject: testprj\nno closing dashes here\n"
        resp = self._post_new(tmp_path, "0001", "R0001", "NR7607", content=malformed)
        assert resp.status_code == 422, resp.text

    def test_edit_normalizes_collapsed_header_and_saves_repaired_body(self, seed_data, tmp_path):
        doc_id = "testprj-__ALL__-0001-CH7608"
        self._create_target_doc(doc_id, tmp_path / "docs" / f"{doc_id}_document.md")

        resp = self._edit(tmp_path, doc_id, self._COLLAPSED_HEADER)
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["normalizations"] == self._COLLAPSED_HEADER_NORMALIZATIONS
        saved = Path(data["stored_path"]).read_text(encoding="utf-8")
        assert saved == self._COLLAPSED_HEADER_NORMALIZED

    def test_edit_dry_run_reports_same_normalization_as_real(self, seed_data, tmp_path):
        doc_id = "testprj-__ALL__-0001-CH7609"
        self._create_target_doc(doc_id, tmp_path / "docs" / f"{doc_id}_document.md")

        from modules.flow_gate.api import inbox_routes
        from modules.flow_gate.services import token_service
        with patch.object(token_service, "_scratch_dir", return_value=tmp_path / "s_edit_dry"):
            raw = token_service.issue(
                project="testprj", group_id="testprj-__ALL__-0001", action_scope="edit",
                doc_ref=doc_id, issued_to="usr_test_001",
            )["raw_token"]
        with patch(
            "modules.flow_gate.rbac.permission_service.has_permission", return_value=True,
        ):
            from starlette.testclient import TestClient
            from fastapi import FastAPI
            app = FastAPI()
            app.include_router(inbox_routes.router)
            dry = TestClient(app).post(
                "/api/v1/inbox",
                json={
                    "project": "testprj", "module": "__ALL__", "group": "0001",
                    "action": "edit", "doc_id": doc_id, "edit_reason": "worker_self",
                    "content": self._COLLAPSED_HEADER, "dry_run": True,
                },
                headers={"Authorization": f"Bearer {raw}"},
            )
        assert dry.status_code == 200, dry.text
        assert dry.json()["would_register"]["normalizations"] == self._COLLAPSED_HEADER_NORMALIZATIONS

    def test_edit_rejects_ambiguous_frontmatter(self, seed_data, tmp_path):
        doc_id = "testprj-__ALL__-0001-CH7610"
        self._create_target_doc(doc_id, tmp_path / "docs" / f"{doc_id}_document.md")
        malformed = "---\nproject: testprj\nno closing dashes here\n"
        resp = self._edit(tmp_path, doc_id, malformed)
        assert resp.status_code == 422, resp.text

    # ── T0004 2.3: recover inside ---, do not false-positive, do not fail-open ──

    _FRONTMATTER_COLLAPSED = (
        "---\n"
        "next_type: NR next_type_detail: 조사레포트 project: testprj module: __ALL__ "
        "group: 0001 title: Collapsed frontmatter case target_id: R0001\n"
        "---\n\n# Body\n"
    )
    _FRONTMATTER_COLLAPSED_NORMALIZED = (
        "---\n"
        "next_type: NR\nnext_type_detail: 조사레포트\nproject: testprj\n"
        "module: __ALL__\ngroup: 0001\ntitle: Collapsed frontmatter case\n"
        "target_id: R0001\n"
        "---\n\n# Body\n"
    )
    _FRONTMATTER_COLLAPSED_NORMALIZATIONS = [
        {"kind": "collapsed_next_header", "line_start": 2, "inserted_breaks": 6}
    ]
    # Completely well-formed frontmatter whose *values* merely name other keys:
    # a title that describes a field, and list/nested-dict entries that quote one.
    # T0004 2.3 forbids judging these on the key name alone — they must register.
    _INNOCENT_KEY_SHAPED_VALUES = (
        "---\n"
        "project: testprj\n"
        "module: __ALL__\n"
        "group_id: testprj-__ALL__-0001\n"
        "type: {doc_type}\n"
        "target_id: R0001\n"
        "title: project: 마이그레이션 안내\n"
        "approved_files:\n"
        "  - target_id: 예시\n"
        "clear_scope:\n"
        "  note: target_id: 예시\n"
        "---\n"
        "short body\n"
    )

    def test_new_recovers_collapsed_frontmatter_then_compares_identity(
        self, seed_data, tmp_path
    ):
        """The reviewed gap: a complete one-line sequence *inside* ``---`` was never
        a normalization candidate, so frontmatter_parse_is_ambiguous 422'd it before
        any identity comparison could happen. T0004 2.3 wants the high-confidence
        pattern recovered first; a matching identity then registers normally."""
        resp = self._post_new(
            tmp_path, "0001", "R0001", "NR7621", content=self._FRONTMATTER_COLLAPSED,
        )
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert data["normalizations"] == self._FRONTMATTER_COLLAPSED_NORMALIZATIONS
        saved = Path(data["stored_path"]).read_text(encoding="utf-8")
        assert saved == self._FRONTMATTER_COLLAPSED_NORMALIZED

    def test_new_doc_path_recovers_collapsed_frontmatter_identically(
        self, seed_data, tmp_path
    ):
        """Same input, file submission: content and doc_path must agree (T0004 2.2)."""
        scratch = tmp_path / "scratch"
        scratch.mkdir(parents=True, exist_ok=True)
        src = scratch / "fm_submission.md"
        src.write_text(self._FRONTMATTER_COLLAPSED, encoding="utf-8")

        resp = self._post_new(tmp_path, "0001", "R0001", "NR7622", doc_path=str(src))
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert data["normalizations"] == self._FRONTMATTER_COLLAPSED_NORMALIZATIONS
        assert Path(data["stored_path"]).read_text(encoding="utf-8") == (
            self._FRONTMATTER_COLLAPSED_NORMALIZED
        )

    def test_new_recovered_collapsed_frontmatter_with_foreign_identity_is_409(
        self, seed_data, tmp_path
    ):
        """Recovery is not amnesty: once repaired, a conflicting identity gets the
        existing 409 — the distinction T0004 2.3 asks for (422 = unrecoverable,
        409 = recovered but declares somebody else)."""
        foreign = self._FRONTMATTER_COLLAPSED.replace(
            "project: testprj module: __ALL__", "project: mailanchor module: ui"
        )
        resp = self._post_new(tmp_path, "0001", "R0001", "NR7623", content=foreign)
        assert resp.status_code == 409, resp.text
        assert "Frontmatter identity mismatch" in resp.json()["error_message"]
        assert "mailanchor" in resp.json()["error_message"]

    def test_new_accepts_frontmatter_whose_values_merely_name_other_keys(
        self, seed_data, tmp_path
    ):
        """Regression for the ambiguity guard's precision: a title that reads
        "project: ..." and list/nested values that quote "target_id: ..." are
        ordinary authored content in a perfectly well-formed frontmatter. Flagging
        them malformed would 422 valid submissions (T0004 2.3)."""
        resp = self._post_new(
            tmp_path, "0001", "R0001", "NR7624",
            content=self._INNOCENT_KEY_SHAPED_VALUES.format(doc_type="NR"),
        )
        assert resp.status_code == 201, resp.text
        assert "normalizations" not in resp.json()

    def test_edit_accepts_frontmatter_whose_values_merely_name_other_keys(
        self, seed_data, tmp_path
    ):
        doc_id = "testprj-__ALL__-0001-CH7625"
        self._create_target_doc(
            doc_id,
            tmp_path / "docs" / f"{doc_id}_document.md",
            target_id="testprj-__ALL__-0001-R0001",
        )
        resp = self._edit(
            tmp_path, doc_id, self._INNOCENT_KEY_SHAPED_VALUES.format(doc_type="CH"),
        )
        assert resp.status_code == 200, resp.text

    def _edit_doc_path(self, tmp_path, doc_id: str, file_body: str, name: str):
        from modules.flow_gate.api import inbox_routes
        from modules.flow_gate.services import token_service
        scratch = tmp_path / "s_edit"
        with patch.object(token_service, "_scratch_dir", return_value=scratch):
            raw = token_service.issue(
                project="testprj", group_id="testprj-__ALL__-0001",
                action_scope="edit", doc_ref=doc_id, issued_to="usr_test_001",
            )["raw_token"]
        scratch.mkdir(parents=True, exist_ok=True)
        src = scratch / name
        # newline="": the fixture file must contain the bytes the test says it
        # does. Path.write_text would translate every "\n" to CRLF here and the
        # EOL assertions downstream would be testing the fixture, not the route.
        with open(src, "w", encoding="utf-8", newline="") as fh:
            fh.write(file_body)
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
                    "doc_path": str(src),
                },
                headers={"Authorization": f"Bearer {raw}"},
            )

    def test_edit_doc_path_recovers_collapsed_frontmatter(self, seed_data, tmp_path):
        """The fourth cell of the new/edit x content/doc_path matrix."""
        doc_id = "testprj-__ALL__-0001-CH7626"
        stored_path = tmp_path / "docs" / f"{doc_id}_document.md"
        self._create_target_doc(
            doc_id, stored_path, target_id="testprj-__ALL__-0001-R0001",
        )
        resp = self._edit_doc_path(
            tmp_path, doc_id, self._FRONTMATTER_COLLAPSED, "fm_edit.md",
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["normalizations"] == self._FRONTMATTER_COLLAPSED_NORMALIZATIONS
        assert stored_path.read_text(encoding="utf-8") == (
            self._FRONTMATTER_COLLAPSED_NORMALIZED
        )

    def test_new_ambiguity_verdict_error_rejects_instead_of_storing(
        self, seed_data, tmp_path
    ):
        """The fail-open the review flagged: the whole guard sat inside a broad
        ``except Exception`` that restored the raw body and kept going — no log, no
        error response, malformed input silently stored. An internal failure in the
        ambiguity verdict must now stop the request and leave nothing behind."""
        from modules.flow_gate.api import inbox_routes
        from modules.flow_gate.db import documents as db_docs

        with patch.object(
            inbox_routes._linter, "frontmatter_parse_is_ambiguous",
            side_effect=RuntimeError("boom"),
        ):
            resp = self._post_new(
                tmp_path, "0001", "R0001", "NR7627",
                content=self._INNOCENT_KEY_SHAPED_VALUES.format(doc_type="NR"),
            )
        assert resp.status_code == 500, resp.text
        assert db_docs.get_by_id("testprj-__ALL__-0001-NR7627") is None
        assert not (tmp_path / "docs" / "NR7627_document.md").exists()

    def test_edit_identity_guard_error_rejects_instead_of_storing(
        self, seed_data, tmp_path
    ):
        """Same rule for the other half of the guard, on the edit path: an error out
        of the identity comparison must not fall through to the save."""
        from modules.flow_gate.api import inbox_routes
        doc_id = "testprj-__ALL__-0001-CH7628"
        stored_path = tmp_path / "docs" / f"{doc_id}_document.md"
        self._create_target_doc(
            doc_id, stored_path, body="# Original conversation",
            target_id="testprj-__ALL__-0001-R0001",
        )
        with patch.object(
            inbox_routes, "_frontmatter_identity_mismatch",
            side_effect=RuntimeError("boom"),
        ):
            resp = self._edit(
                tmp_path, doc_id, self._INNOCENT_KEY_SHAPED_VALUES.format(doc_type="CH"),
            )
        assert resp.status_code == 500, resp.text
        assert stored_path.read_text(encoding="utf-8") == "# Original conversation"

    def test_new_normalizer_error_still_reaches_the_guard(self, seed_data, tmp_path):
        """The two failure kinds are deliberately split (T0004 2.3). A repair is not
        a gate, so a broken normalizer must not 500 the request — but it must not
        become a bypass either: the guard still runs on the un-repaired body, so
        this malformed frontmatter is still rejected with 422, not stored."""
        from modules.flow_gate.api import inbox_routes
        from modules.flow_gate.db import documents as db_docs

        malformed = (
            "---\n"
            "title: T next_type_detail: 조사레포트 project: testprj module: __ALL__ "
            "group: 0001 target_id: R0001\n"
            "---\n"
            "short body\n"
        )
        with patch.object(
            inbox_routes._linter, "normalize_submission_header",
            side_effect=RuntimeError("boom"),
        ):
            resp = self._post_new(tmp_path, "0001", "R0001", "NR7629", content=malformed)
        assert resp.status_code == 422, resp.text
        assert db_docs.get_by_id("testprj-__ALL__-0001-NR7629") is None

    # ── T0004 2.1/2.2: the bytes stored are the bytes checked, EOL included ──
    #
    # Path.write_text(..., encoding="utf-8") opens in *text* mode, where every
    # "\n" is translated to os.linesep on the way out. On this Windows
    # deployment that stores an LF body as CRLF and an already-CRLF body as
    # CRCRLF: the file on disk stops being the string every guard inspected and
    # a no-op CRLF resubmission comes back corrupted. read_text() on the way in
    # hides it (universal newlines decode CRLF, CRCRLF and LF all to "\n"), so
    # these assertions deliberately compare raw bytes.

    _CRLF_DOC = (
        "next_type: NR\r\nnext_type_detail: 조사레포트\r\nproject: testprj\r\n"
        "module: __ALL__\r\ngroup: 0001\r\ntitle: CRLF round trip\r\n"
        "target_id: R0001\r\n\r\nBody text, unchanged.\r\n"
    )
    _LF_DOC = _CRLF_DOC.replace("\r\n", "\n")
    _CRLF_COLLAPSED = (
        "next_type: NR next_type_detail: 조사레포트 project: testprj "
        "module: __ALL__ group: 0001 title: CRLF collapsed target_id: R0001"
        "\r\n\r\nBody text.\r\n"
    )
    _CRLF_COLLAPSED_NORMALIZED = (
        "next_type: NR\r\nnext_type_detail: 조사레포트\r\nproject: testprj\r\n"
        "module: __ALL__\r\ngroup: 0001\r\ntitle: CRLF collapsed\r\n"
        "target_id: R0001\r\n\r\nBody text.\r\n"
    )

    def test_new_content_crlf_body_is_stored_byte_for_byte(self, seed_data, tmp_path):
        """An already-correct CRLF submission is a no-op: same bytes in, same
        bytes out, and nothing reported as normalized."""
        resp = self._post_new(tmp_path, "0001", "R0001", "NR7631", content=self._CRLF_DOC)
        assert resp.status_code == 201, resp.text
        assert "normalizations" not in resp.json()
        raw = Path(resp.json()["stored_path"]).read_bytes()
        assert raw == self._CRLF_DOC.encode("utf-8")
        assert b"\r\r\n" not in raw

    def test_new_content_lf_body_is_stored_byte_for_byte(self, seed_data, tmp_path):
        resp = self._post_new(tmp_path, "0001", "R0001", "NR7632", content=self._LF_DOC)
        assert resp.status_code == 201, resp.text
        raw = Path(resp.json()["stored_path"]).read_bytes()
        assert raw == self._LF_DOC.encode("utf-8")
        assert b"\r" not in raw

    def test_new_doc_path_crlf_file_is_stored_byte_for_byte(self, seed_data, tmp_path):
        scratch = tmp_path / "scratch"
        scratch.mkdir(parents=True, exist_ok=True)
        src = scratch / "crlf_submission.md"
        with open(src, "w", encoding="utf-8", newline="") as fh:
            fh.write(self._CRLF_DOC)

        resp = self._post_new(tmp_path, "0001", "R0001", "NR7633", doc_path=str(src))
        assert resp.status_code == 201, resp.text
        assert "normalizations" not in resp.json()
        assert Path(resp.json()["stored_path"]).read_bytes() == self._CRLF_DOC.encode("utf-8")

    def test_new_crlf_collapsed_header_is_repaired_in_crlf(self, seed_data, tmp_path):
        """A genuine repair must not smuggle a second line-ending style into a
        CRLF document either."""
        resp = self._post_new(
            tmp_path, "0001", "R0001", "NR7634", content=self._CRLF_COLLAPSED,
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["normalizations"] == [
            {"kind": "collapsed_next_header", "line_start": 1, "inserted_breaks": 6}
        ]
        raw = Path(resp.json()["stored_path"]).read_bytes()
        assert raw == self._CRLF_COLLAPSED_NORMALIZED.encode("utf-8")
        assert raw.count(b"\n") == raw.count(b"\r\n")

    def test_edit_content_crlf_body_is_stored_byte_for_byte(self, seed_data, tmp_path):
        doc_id = "testprj-__ALL__-0001-CH7635"
        stored_path = tmp_path / "docs" / f"{doc_id}_document.md"
        self._create_target_doc(
            doc_id, stored_path, target_id="testprj-__ALL__-0001-R0001",
        )
        resp = self._edit(tmp_path, doc_id, self._CRLF_DOC)
        assert resp.status_code == 200, resp.text
        raw = stored_path.read_bytes()
        assert raw == self._CRLF_DOC.encode("utf-8")
        assert b"\r\r\n" not in raw

    def test_edit_content_lf_body_is_stored_byte_for_byte(self, seed_data, tmp_path):
        doc_id = "testprj-__ALL__-0001-CH7636"
        stored_path = tmp_path / "docs" / f"{doc_id}_document.md"
        self._create_target_doc(
            doc_id, stored_path, target_id="testprj-__ALL__-0001-R0001",
        )
        resp = self._edit(tmp_path, doc_id, self._LF_DOC)
        assert resp.status_code == 200, resp.text
        raw = stored_path.read_bytes()
        assert raw == self._LF_DOC.encode("utf-8")
        assert b"\r" not in raw

    def test_edit_doc_path_crlf_file_is_stored_byte_for_byte(self, seed_data, tmp_path):
        doc_id = "testprj-__ALL__-0001-CH7637"
        stored_path = tmp_path / "docs" / f"{doc_id}_document.md"
        self._create_target_doc(
            doc_id, stored_path, target_id="testprj-__ALL__-0001-R0001",
        )
        resp = self._edit_doc_path(
            tmp_path, doc_id, self._CRLF_DOC, "crlf_edit.md",
        )
        assert resp.status_code == 200, resp.text
        assert stored_path.read_bytes() == self._CRLF_DOC.encode("utf-8")

    # ── T0004 2.3: a BOM must not be a way past the frontmatter guards ──

    _BOM = "\ufeff"
    _FOREIGN_FRONTMATTER = (
        "---\n"
        "project: mailanchor\n"
        "module: ui\n"
        "title: somebody else's document\n"
        "---\n"
        "short body\n"
    )
    _MALFORMED_FRONTMATTER = (
        "---\n"
        "title: T project: testprj module: __ALL__ group: 0001 target_id: R0001\n"
        "---\n"
        "short body\n"
    )

    def test_new_rejects_bom_prefixed_foreign_frontmatter(self, seed_data, tmp_path):
        """U+FEFF is not whitespace, so `text.lstrip().startswith("---")` reported
        "not frontmatter" and skipped both the ambiguity verdict and the identity
        comparison — while the normalizer recognized the very same block."""
        resp = self._post_new(
            tmp_path, "0001", "R0001", "NR7641",
            content=self._BOM + self._FOREIGN_FRONTMATTER,
        )
        assert resp.status_code == 409, resp.text
        assert "mailanchor" in resp.json()["error_message"]

    def test_new_rejects_bom_prefixed_malformed_frontmatter(self, seed_data, tmp_path):
        resp = self._post_new(
            tmp_path, "0001", "R0001", "NR7642",
            content=self._BOM + self._MALFORMED_FRONTMATTER,
        )
        assert resp.status_code == 422, resp.text

    def test_edit_rejects_bom_prefixed_foreign_frontmatter(self, seed_data, tmp_path):
        doc_id = "testprj-__ALL__-0001-CH7643"
        stored_path = tmp_path / "docs" / f"{doc_id}_document.md"
        self._create_target_doc(
            doc_id, stored_path, body="# Original conversation",
            target_id="testprj-__ALL__-0001-R0001",
        )
        resp = self._edit(tmp_path, doc_id, self._BOM + self._FOREIGN_FRONTMATTER)
        assert resp.status_code == 409, resp.text
        assert stored_path.read_text(encoding="utf-8") == "# Original conversation"

    def test_edit_rejects_bom_prefixed_malformed_frontmatter(self, seed_data, tmp_path):
        doc_id = "testprj-__ALL__-0001-CH7644"
        stored_path = tmp_path / "docs" / f"{doc_id}_document.md"
        self._create_target_doc(
            doc_id, stored_path, body="# Original conversation",
            target_id="testprj-__ALL__-0001-R0001",
        )
        resp = self._edit(tmp_path, doc_id, self._BOM + self._MALFORMED_FRONTMATTER)
        assert resp.status_code == 422, resp.text
        assert stored_path.read_text(encoding="utf-8") == "# Original conversation"

    # ── T0004 2.3: partial chains and fake closing delimiters stay closed ──

    def test_new_rejects_a_two_pair_collapsed_frontmatter_line(self, seed_data, tmp_path):
        """The shortest partial chain — one line, two known keys. It used to fall
        below the "two *embedded* labels" threshold (the key the parser kept was
        not counted) and so was never classified as malformed at all."""
        body = (
            "---\n"
            "project: testprj\n"
            "module: __ALL__ group: 0001\n"
            "title: two pair\n"
            "---\n"
            "short body\n"
        )
        resp = self._post_new(tmp_path, "0001", "R0001", "NR7645", content=body)
        assert resp.status_code == 422, resp.text

    def test_new_rejects_frontmatter_closed_only_by_hyphens_inside_a_scalar(
        self, seed_data, tmp_path
    ):
        """Three hyphens inside an ordinary value are not a closing delimiter: only a
        standalone "---" line is. Accepting the first literal match let an
        unclosed block parse "successfully" and fail open."""
        body = (
            "---\n"
            "title: a --- b\n"
            "project: testprj\n"
            "no closing delimiter line anywhere\n"
        )
        resp = self._post_new(tmp_path, "0001", "R0001", "NR7646", content=body)
        assert resp.status_code == 422, resp.text

    def test_new_rejects_a_parse_truncated_before_the_real_delimiter(
        self, seed_data, tmp_path
    ):
        """Closed, but parse_yaml_header cuts the block at the "---" inside the
        title, so `project` never reaches the header dict and the identity
        comparison would have compared nothing."""
        body = (
            "---\n"
            "title: a --- b\n"
            "project: mailanchor\n"
            "---\n"
            "short body\n"
        )
        resp = self._post_new(tmp_path, "0001", "R0001", "NR7647", content=body)
        assert resp.status_code == 422, resp.text


# ── 8-2. 이관 완료 대화(CH)의 전체 본문 교체 차단 (0432.0003-NR §7-1) ────────

class TestInboxEditConversationFullBodyGuard:
    """0344 TR0008 후속. 대화의 정본은 ``conversation_turns`` 표로 옮겨졌는데 본문을
    통째로 덮어쓰는 옛 인박스 edit 경로가 살아 있었다(0432.0003-NR §5). 0344.0008-TR 이
    그 마무리를 시도했다가 반려된 뒤 그룹 0344 에는 후속이 없었다(NR §4).

    판정과 문구는 0344.0005-L §2-16 원문, 봉투는 0344.0004-P §0-5 의 워커 계열이다.
    문구는 상수를 import 하지 않고 여기 글자 그대로 적는다 — 상수와 시험이 같은 곳을
    보면 문구가 틀려도 둘이 나란히 틀린다. 이것은 워커가 받는 전선 위의 계약이다.
    """

    _MESSAGE = (
        "This conversation no longer accepts a full-body edit. "
        "Append one turn: POST /api/v1/conversation/{doc_id}/turn"
    )

    def _expected_message(self, doc_id: str) -> str:
        return self._MESSAGE.format(doc_id=doc_id)

    def _create_doc(self, doc_id, stored_path, body="# Original", target_id=None, type_code="CH"):
        TestInboxEditDuplicateBodyGuard()._create_target_doc(
            doc_id, stored_path, body=body, target_id=target_id, type_code=type_code,
        )

    def _mark_migrated(self, doc_id: str) -> None:
        """실제 이관 경로와 같은 함수로 migrated 상태를 만든다(손으로 INSERT 하지 않는다)."""
        from modules.flow_gate.db import conversation_turns
        assert conversation_turns.acquire_migration_lock(doc_id, "test-owner")
        conversation_turns.mark_migrated(doc_id, "test-owner", "intro", 0)
        assert conversation_turns.migration_state(doc_id) == "migrated"

    def _edit(self, tmp_path, doc_id: str, content: str, dry_run: bool = False):
        """``TestInboxEditDuplicateBodyGuard._edit`` 과 같은 요청에 dry_run 스위치만 더한 것.
        토큰이 소모됐는지 확인해야 해서 raw 토큰을 함께 돌려준다."""
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
        payload = {
            "project": "testprj", "module": "__ALL__", "group": "0001",
            "action": "edit", "doc_id": doc_id, "edit_reason": "worker_self",
            "content": content,
        }
        if dry_run:
            payload["dry_run"] = True
        with patch(
            "modules.flow_gate.rbac.permission_service.has_permission", return_value=True,
        ):
            from starlette.testclient import TestClient
            from fastapi import FastAPI
            app = FastAPI()
            app.include_router(inbox_routes.router)
            resp = TestClient(app).post(
                "/api/v1/inbox", json=payload, headers={"Authorization": f"Bearer {raw}"},
            )
        return resp, raw

    def test_migrated_conversation_rejects_full_body_edit(self, seed_data, tmp_path):
        """이관이 끝난 대화 + 자기 문서가 맞는 본문 → 409, 문구 정확 일치, 파일 그대로."""
        doc_id = "testprj-__ALL__-0001-CH7601"
        stored_path = tmp_path / "docs" / f"{doc_id}_document.md"
        self._create_doc(doc_id, stored_path, body="# Original conversation")
        self._mark_migrated(doc_id)

        resp, _raw = self._edit(tmp_path, doc_id, "# A wholesale rewrite of the transcript")

        assert resp.status_code == 409, resp.text
        payload = resp.json()
        assert payload["error_message"] == self._expected_message(doc_id)
        # 0344.0004-P §0-5 워커 봉투 — 세션 계열의 {"detail": ...} 가 섞이면 안 된다.
        assert payload["ok"] is False
        assert payload["http_status"] == 409
        assert payload["help_url"]
        assert "detail" not in payload
        # 정본은 표에 있고 파일은 껍데기다. 거절이 그 껍데기조차 건드리지 않았음을 본다.
        assert stored_path.read_text() == "# Original conversation"

    def test_dry_run_gets_the_same_rejection_and_changes_nothing(self, seed_data, tmp_path):
        """판정이 _maybe_dry_run 보다 앞이므로 dry_run 도 같은 409 다(부작용 없음)."""
        from modules.flow_gate.db import documents as db_docs
        from modules.flow_gate.services import token_service

        doc_id = "testprj-__ALL__-0001-CH7602"
        stored_path = tmp_path / "docs" / f"{doc_id}_document.md"
        self._create_doc(doc_id, stored_path, body="# Original conversation")
        self._mark_migrated(doc_id)
        before = db_docs.get_by_id(doc_id)

        resp, raw = self._edit(
            tmp_path, doc_id, "# A wholesale rewrite of the transcript", dry_run=True,
        )

        assert resp.status_code == 409, resp.text
        assert resp.json()["error_message"] == self._expected_message(doc_id)
        assert stored_path.read_text() == "# Original conversation"
        after = db_docs.get_by_id(doc_id)
        assert after["revision_no"] == before["revision_no"]
        # 토큰도 그대로 살아 있다 — verify 는 consumed_at 이 찍혀 있으면 401 로 막는다.
        assert token_service.verify(raw)["doc_ref"] == doc_id

    def test_foreign_frontmatter_gets_the_conversation_block_not_identity(
        self, seed_data, tmp_path
    ):
        """이관된 대화에 남의 프론트매터를 붙여 보내도 identity 오류가 아니라 대화 차단이다.

        L §4-1 의 순서 원칙(그룹 차원의 사실 → 본문을 들여다보는 검사) 때문이고, 그것이
        의도다: 워커에게는 "네 문서가 아니다"보다 "턴으로 보내라"가 실행 가능한 안내다.
        """
        doc_id = "testprj-__ALL__-0001-CH7603"
        stored_path = tmp_path / "docs" / f"{doc_id}_document.md"
        self._create_doc(
            doc_id, stored_path, body="# Original conversation",
            target_id="testprj-__ALL__-0001-R0001",
        )
        self._mark_migrated(doc_id)
        foreign_prefix = (
            "---\n"
            "project: mailanchor\n"
            "module: ui\n"
            "group_id: mailanchor.ui.0001\n"
            "doc_number: 0004-CH\n"
            "type: CH\n"
            "target_id: mailanchor.ui.0001.0003-CH\n"
            "title: Source conversation\n"
            "---\n"
            "short copied prefix\n"
        )

        resp, _raw = self._edit(tmp_path, doc_id, foreign_prefix)

        assert resp.status_code == 409, resp.text
        message = resp.json()["error_message"]
        assert message == self._expected_message(doc_id)
        assert "Frontmatter identity mismatch" not in message
        assert stored_path.read_text() == "# Original conversation"

    def test_legacy_conversation_still_accepts_full_body_edit(self, seed_data, tmp_path):
        """conversation_docs 행이 없는 대화(LEGACY)는 아직 파일이 정본이라 그대로 저장된다
        (L §2-16: "이관되지 않은 문서에는 이 분기를 걸지 않는다")."""
        from modules.flow_gate.db import conversation_turns

        doc_id = "testprj-__ALL__-0001-CH7604"
        stored_path = tmp_path / "docs" / f"{doc_id}_document.md"
        self._create_doc(doc_id, stored_path, body="# Original conversation")
        assert conversation_turns.migration_state(doc_id) == "pending"

        resp, _raw = self._edit(tmp_path, doc_id, "# A legacy conversation still edits fine")

        assert resp.status_code == 200, resp.text

    def test_in_progress_conversation_still_accepts_full_body_edit(self, seed_data, tmp_path):
        """이관 중(in_progress)도 아직 migrated 가 아니다 — 걸지 않는다."""
        from modules.flow_gate.db import conversation_turns

        doc_id = "testprj-__ALL__-0001-CH7605"
        stored_path = tmp_path / "docs" / f"{doc_id}_document.md"
        self._create_doc(doc_id, stored_path, body="# Original conversation")
        assert conversation_turns.acquire_migration_lock(doc_id, "test-owner")
        assert conversation_turns.migration_state(doc_id) == "in_progress"

        resp, _raw = self._edit(tmp_path, doc_id, "# Mid-migration edit still lands")

        assert resp.status_code == 200, resp.text

    def test_non_conversation_document_is_untouched(self, seed_data, tmp_path):
        """대화가 아닌 문서에는 판정이 끼어들지 않는다(회귀)."""
        doc_id = "testprj-__ALL__-0001-NR7606"
        stored_path = tmp_path / "docs" / f"{doc_id}_document.md"
        self._create_doc(doc_id, stored_path, body="# Original report", type_code="NR")

        resp, _raw = self._edit(tmp_path, doc_id, "# An ordinary report edit")

        assert resp.status_code == 200, resp.text


# ── 9. Cold-start backfill of body fingerprints (NR0003 §5.2) ───────────────

class TestBackfillContentFingerprint:
    """Documents created before the dup-body guard shipped carry meta=NULL, so the
    guard can never match a clone of them (the cold-start gap that makes the
    contamination recur — B0001 "4회차"). The backfill tool computes the same
    fingerprints the live guard uses and writes them into meta additively.
    """

    _BIG_BODY = "# Investigation Report\n\n" + ("Root cause analysis line. " * 120)
    _SHORT_BODY = "조사지시 가 승인되었습니다."

    def _seed_doc(self, doc_id: str, body: str, root, *, meta=None) -> None:
        from modules.flow_gate.db import documents as db_docs
        rel = f"docs/{doc_id}.md"
        abs_path = root / rel
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        abs_path.write_text(body, encoding="utf-8")
        db_docs.create({
            "doc_id": doc_id, "project_id": "testprj", "type_code": "NR", "seq": 1,
            "title": doc_id, "group_id": "testprj-__ALL__-0001", "module": "__ALL__",
            "owner_id": "usr_test_001", "file_path": rel, "meta": meta,
        })

    def test_backfill_fills_substantial_skips_short_and_is_idempotent(self, seed_data, tmp_path):
        import json
        from modules.flow_gate.db import documents as db_docs
        from tools import backfill_content_fingerprint as bf

        root = tmp_path / "bf_store"
        self._seed_doc("testprj-__ALL__-0001-NR9101", self._BIG_BODY, root)
        self._seed_doc("testprj-__ALL__-0001-NR9102", self._SHORT_BODY, root)
        # A doc that already has unrelated meta — backfill must preserve it.
        self._seed_doc(
            "testprj-__ALL__-0001-NR9103", self._BIG_BODY + " v2", root,
            meta=json.dumps({"related_doc_ids": ["testprj-__ALL__-0001-R0001"]}),
        )

        with patch(
            "modules.flow_gate.storage.paths.get_storage_root", return_value=root,
        ):
            stats = bf.backfill(dry_run=False)
            assert stats["updated"] >= 2 and stats["short"] >= 1

            big = json.loads(db_docs.get_by_id("testprj-__ALL__-0001-NR9101")["meta"])
            assert "content_sha256" in big and "content_sha256_norm" in big

            short_meta = db_docs.get_by_id("testprj-__ALL__-0001-NR9102")["meta"]
            assert not short_meta or "content_sha256" not in short_meta

            keep = json.loads(db_docs.get_by_id("testprj-__ALL__-0001-NR9103")["meta"])
            assert keep["related_doc_ids"] == ["testprj-__ALL__-0001-R0001"]
            assert "content_sha256" in keep

            # Idempotent: a second pass rewrites nothing for the rows just filled.
            again = bf.backfill(dry_run=False)
            assert again["updated"] == 0

