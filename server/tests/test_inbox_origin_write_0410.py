"""flowgate.default.0410 TR0009 rework — the write-side half of the origin snapshot.

081_document_origin_snapshot.sql added `documents.origin_provider_name` /
`origin_ai_run_id`, and T0008's read paths (list/tree/detail) were wired to pass
them through. But no caller of `db_docs.create()` ever populated them, so every
document — past AND future — kept both columns NULL forever; the "AI · {provider}"
badge could never render for any real document (only ever "미상"). That is the
literal, verifiable meaning of the TR0009 rev0 rejection "아무것도 변한게 없다".

This covers the fix in `inbox_routes.py`: the real `POST /api/v1/inbox` (action=new)
path now resolves the submitting token's `ai_run_id` to a provider name (live run
first, persisted `ai_invoke_runs` row as fallback — mirroring
`ai_invoke_service.get_run_detail`'s own memory-then-DB order) and stores both on
the created row.
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
os.environ["FLOWGATE_TOKEN_PEPPER_ACTIVE_ID"] = "owtest1"
os.environ["FLOWGATE_TOKEN_PEPPER_owtest1"] = "test-pepper-value-456"

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

    def fetch_one(self, sql: str, params=None):
        row = self._conn.execute(sql, params or []).fetchone()
        return dict(row) if row else None

    def fetch_all(self, sql: str, params=None):
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

    def fetch_one(self):
        if self._last_cursor is None:
            return None
        row = self._last_cursor.fetchone()
        return dict(row) if row else None

    def fetch_all(self):
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
    _orig_active_id = os.environ.get("FLOWGATE_TOKEN_PEPPER_ACTIVE_ID")
    os.environ["FLOWGATE_TOKEN_PEPPER_ACTIVE_ID"] = "owtest1"

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
def seed_data(tmp_db):
    from modules.flow_gate.db import documents as db_docs
    from modules.flow_gate.db import groups as db_groups
    from modules.flow_gate.db import projects, users
    from modules.flow_gate.db.connection import get_store, now_iso

    projects.create({"project_id": "owproj", "project_name": "Origin Write Test"})
    users.create({
        "user_id": "usr_ow_001",
        "username": "owuser",
        "email": "ow@example.com",
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
        ["usr_ow_001", "owproj", "role_worker", now],
    )
    db_groups.create({
        "group_id": "owproj-__ALL__-0001",
        "project_id": "owproj",
        "module": "__ALL__",
        "title": "Origin Write Test Group",
    })
    store._execute(
        "INSERT OR IGNORE INTO document_types "
        "(project_id, type_code, type_name, series, is_system, is_active, sort_order, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        [None, "NR", "New Request", "work", 1, 1, 0, now, now],
    )
    store._execute(
        "INSERT OR IGNORE INTO document_types "
        "(project_id, type_code, type_name, series, is_system, is_active, sort_order, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        [None, "R", "Requirement", "general", 1, 1, 0, now, now],
    )
    db_docs.create({
        "doc_id": "owproj-__ALL__-0001-R0001",
        "project_id": "owproj",
        "type_code": "R",
        "seq": 1,
        "title": "Root Requirement",
        "group_id": "owproj-__ALL__-0001",
        "module": "__ALL__",
        "owner_id": "usr_ow_001",
    })
    yield


def _issue_token(tmp_path, *, ai_run_id=None):
    from modules.flow_gate.services import token_service

    with patch.object(token_service, "_scratch_dir", return_value=tmp_path / "scratch"):
        result = token_service.issue(
            project="owproj",
            group_id="owproj-__ALL__-0001",
            action_scope="new",
            doc_ref="owproj-__ALL__-0001-R0001",
            issued_to="usr_ow_001",
            ai_run_id=ai_run_id,
        )
    return result["raw_token"]


def _post_new(raw_token, tmp_path, *, seq_label: str, content: str):
    from fastapi import FastAPI
    from starlette.testclient import TestClient

    from modules.flow_gate.api import inbox_routes

    with patch(
        "modules.flow_gate.api.inbox_routes.get_storage_root",
        return_value=tmp_path,
    ), patch(
        "modules.flow_gate.api.inbox_routes.document_path",
        return_value=tmp_path / "docs" / f"{seq_label}_document.md",
    ), patch(
        "modules.flow_gate.api.inbox_routes.numbering_service.reserve_document",
        return_value=seq_label,
    ), patch(
        "modules.flow_gate.rbac.permission_service.has_permission",
        return_value=True,
    ):
        app = FastAPI()
        app.include_router(inbox_routes.router)
        client = TestClient(app)
        return client.post(
            "/api/v1/inbox",
            json={
                "project": "owproj",
                "module": "__ALL__",
                "group": "0001",
                "action": "new",
                "target_id": "R0001",
                "doc_type": "NR",
                "content": content,
            },
            headers={"Authorization": f"Bearer {raw_token}"},
        )


class TestInboxOriginSnapshotWrite:
    def test_live_run_snapshots_provider_and_run_id(self, seed_data, tmp_path):
        """TS0010 TC-1: a live run's actual provider is frozen onto the new document."""
        from modules.flow_gate.db import documents as db_docs
        from modules.flow_gate.services import ai_invoke_service

        run_id = "aiv_owtest_000001"
        raw = _issue_token(tmp_path, ai_run_id=run_id)

        with patch.object(
            ai_invoke_service,
            "get_run_record",
            return_value={"provider": {"id": "prov_1", "name": "Claude Sonnet 5"}},
        ):
            resp = _post_new(raw, tmp_path, seq_label="NR9201", content="# live run")

        assert resp.status_code == 201
        doc = db_docs.get_by_id("owproj-__ALL__-0001-NR9201")
        assert doc is not None
        assert doc["origin_ai_run_id"] == run_id
        assert doc["origin_provider_name"] == "Claude Sonnet 5"

    def test_persisted_run_row_used_when_run_no_longer_live(self, seed_data, tmp_path):
        """A run finalized in an earlier process (not in ai_invoke_service memory) falls
        back to the persisted ai_invoke_runs row — same order get_run_detail() uses."""
        from modules.flow_gate.db import ai_invoke_runs as db_ai_invoke_runs
        from modules.flow_gate.db import documents as db_docs
        from modules.flow_gate.db.connection import now_iso
        from modules.flow_gate.services import ai_invoke_service

        run_id = "aiv_owtest_000002"
        db_ai_invoke_runs.upsert({
            "run_id": run_id,
            "group_id": "owproj-__ALL__-0001",
            "project_id": "owproj",
            "doc_ref": "owproj-__ALL__-0001-R0001",
            "mode": "single",
            "provider_id": None,
            "provider_name": "Codex GPT-5.6",
            "started_at": now_iso(),
            "finished_at": now_iso(),
            "created_at": now_iso(),
            "updated_at": now_iso(),
        })
        raw = _issue_token(tmp_path, ai_run_id=run_id)

        with patch.object(ai_invoke_service, "get_run_record", return_value=None):
            resp = _post_new(raw, tmp_path, seq_label="NR9202", content="# persisted run")

        assert resp.status_code == 201
        doc = db_docs.get_by_id("owproj-__ALL__-0001-NR9202")
        assert doc is not None
        assert doc["origin_ai_run_id"] == run_id
        assert doc["origin_provider_name"] == "Codex GPT-5.6"

    def test_no_ai_run_id_leaves_origin_fields_null(self, seed_data, tmp_path):
        """TS0010 TC-7: a token without ai_run_id must not be guessed into an origin."""
        from modules.flow_gate.db import documents as db_docs

        raw = _issue_token(tmp_path, ai_run_id=None)

        resp = _post_new(raw, tmp_path, seq_label="NR9203", content="# no run id")

        assert resp.status_code == 201
        doc = db_docs.get_by_id("owproj-__ALL__-0001-NR9203")
        assert doc is not None
        assert doc["origin_ai_run_id"] is None
        assert doc["origin_provider_name"] is None


class TestResolveOriginProviderName:
    def test_no_run_id_short_circuits(self):
        from modules.flow_gate.api.inbox_routes import _resolve_origin_provider_name

        assert _resolve_origin_provider_name(None) is None
        assert _resolve_origin_provider_name("") is None

    def test_lookup_failure_is_swallowed_not_raised(self):
        from modules.flow_gate.api import inbox_routes
        from modules.flow_gate.services import ai_invoke_service

        with patch.object(ai_invoke_service, "get_run_record", side_effect=RuntimeError("boom")):
            assert inbox_routes._resolve_origin_provider_name("aiv_whatever") is None

    def test_live_run_without_provider_name_returns_none(self):
        from modules.flow_gate.api import inbox_routes
        from modules.flow_gate.services import ai_invoke_service

        with patch.object(ai_invoke_service, "get_run_record", return_value={"provider": None}):
            assert inbox_routes._resolve_origin_provider_name("aiv_no_provider") is None
