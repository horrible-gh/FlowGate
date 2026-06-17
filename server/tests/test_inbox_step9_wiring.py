from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

os.environ["TESTING"] = "1"
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
os.environ["FLOWGATE_TOKEN_PEPPER_ACTIVE_ID"] = "test1"
os.environ["FLOWGATE_TOKEN_PEPPER_test1"] = "test-pepper-value-123"

_SERVER_DIR = Path(__file__).resolve().parents[1]
_SCHEMA_DIR = _SERVER_DIR / "sql" / "migrations" / "sqlite"
sys.path.insert(0, str(_SERVER_DIR))


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
def seed_data(tmp_db):
    from modules.flow_gate.db import documents as db_docs
    from modules.flow_gate.db import groups as db_groups
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
    store._execute(
        "INSERT OR IGNORE INTO roles (role_id, role_name, created_at, updated_at) VALUES (?,?,?,?)",
        ["role_worker", "Worker", now, now],
    )
    for perm in ["document.create", "document.read", "document.update"]:
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
        ["usr_test_001", "testprj", "role_worker", now],
    )
    db_groups.create({
        "group_id": "testprj-__ALL__-0001",
        "project_id": "testprj",
        "module": "__ALL__",
        "title": "Test Group",
    })
    store._execute(
        "INSERT OR IGNORE INTO document_types (project_id, type_code, type_name, series, is_system, is_active, sort_order, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
        [None, "NR", "New Request", "work", 1, 1, 0, now, now],
    )
    store._execute(
        "INSERT OR IGNORE INTO document_types (project_id, type_code, type_name, series, is_system, is_active, sort_order, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
        [None, "R", "Requirement", "general", 1, 1, 1, now, now],
    )
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


def _build_client():
    from modules.flow_gate.api import inbox_routes

    app = FastAPI()
    app.include_router(inbox_routes.router)
    return TestClient(app)


def _issue_token(tmp_path, action_scope: str, doc_ref: str | None) -> str:
    from modules.flow_gate.services import token_service

    with patch.object(token_service, "_scratch_dir", return_value=tmp_path / "scratch"):
        issued = token_service.issue(
            project="testprj",
            group_id="testprj-__ALL__-0001",
            action_scope=action_scope,
            doc_ref=doc_ref,
            issued_to="usr_test_001",
        )
    return issued["raw_token"]


def _wait_for_asyncmock(mock: AsyncMock, minimum: int):
    for _ in range(40):
        if mock.await_count >= minimum:
            return
        time.sleep(0.05)
    raise AssertionError(f"publish_event awaited {mock.await_count}, expected at least {minimum}")


def test_inbox_new_pushes_events(seed_data, tmp_path):
    client = _build_client()
    raw = _issue_token(tmp_path, "new", "testprj-__ALL__-0001-R0001")
    publish_event = AsyncMock()

    with patch("modules.flow_gate.api.v1.events.publisher.publish_event", publish_event), patch(
        "modules.flow_gate.api.v1.group_routes.get_next_action_candidates",
        return_value=[{"action_code": "create_ds", "prev_doc_id": "R0001"}],
    ), patch(
        "modules.flow_gate.api.inbox_routes.get_storage_root",
        return_value=tmp_path,
    ), patch(
        "modules.flow_gate.api.inbox_routes.document_path",
        return_value=tmp_path / "docs" / "NR0001_document.md",
    ), patch(
        "modules.flow_gate.api.inbox_routes.numbering_service.reserve_document",
        return_value="NR0001",
    ):
        resp = client.post(
            "/api/v1/inbox",
            json={
                "project": "testprj",
                "module": "__ALL__",
                "group_name": "testprj-__ALL__-0001",
                "action": "new",
                "prev_doc_id": "testprj-__ALL__-0001-R0001",
                "doc_type": "NR",
                "content": "# New",
            },
            headers={"Authorization": f"Bearer {raw}"},
        )

    assert resp.status_code == 201
    _wait_for_asyncmock(publish_event, 4)
    event_types = [call.args[0].event_type for call in publish_event.await_args_list]
    assert "file_explorer_refresh" in event_types
    assert "document_explorer_refresh" in event_types
    assert "group_view_refresh" in event_types
    assert "notification_new_action_candidate" in event_types


def test_inbox_edit_pushes_events(seed_data, tmp_path):
    from modules.flow_gate.db import documents as db_docs

    db_docs.delete("testprj-__ALL__-0001-NR0001")
    target = tmp_path / "docs" / "testprj-__ALL__-0001-NR0001.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("# Original", encoding="utf-8")
    db_docs.create({
        "doc_id": "testprj-__ALL__-0001-NR0001",
        "project_id": "testprj",
        "type_code": "NR",
        "seq": 2,
        "title": "testprj-__ALL__-0001-NR0001",
        "group_id": "testprj-__ALL__-0001",
        "module": "__ALL__",
        "owner_id": "usr_test_001",
        "file_path": str(target),
        "revision_no": 0,
    })

    client = _build_client()
    raw = _issue_token(tmp_path, "edit", "testprj-__ALL__-0001-NR0001")
    publish_event = AsyncMock()

    with patch("modules.flow_gate.api.v1.events.publisher.publish_event", publish_event):
        resp = client.post(
            "/api/v1/inbox",
            json={
                "project": "testprj",
                "module": "__ALL__",
                "group_name": "testprj-__ALL__-0001",
                "action": "edit",
                "doc_id": "testprj-__ALL__-0001-NR0001",
                "edit_reason": "worker_self",
                "content": "# Updated",
            },
            headers={"Authorization": f"Bearer {raw}"},
        )

    assert resp.status_code == 200
    _wait_for_asyncmock(publish_event, 4)
    event_types = [call.args[0].event_type for call in publish_event.await_args_list]
    assert "file_explorer_refresh" in event_types
    assert "document_explorer_refresh" in event_types
    assert "group_view_refresh" in event_types
    assert "edit_marker_added" in event_types
