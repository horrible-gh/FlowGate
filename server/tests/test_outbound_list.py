from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

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
    # Migration 064 rebuilds tokens from a legacy column list and can erase the
    # column added by 063 when tests replay every migration while swallowing
    # duplicate-column errors. Keep this fixture aligned with the runtime model.
    token_columns = {
        row["name"] for row in mock_db._conn.execute("PRAGMA table_info(tokens)")
    }
    if "continuation_instruction_mode" not in token_columns:
        mock_db._conn.execute(
            "ALTER TABLE tokens ADD COLUMN continuation_instruction_mode TEXT"
        )
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
    projects.create({"project_id": "OFFPRJ", "project_name": "Off Project"})
    users.create({
        "user_id": "usr_test_001",
        "username": "testuser",
        "email": "test@example.com",
        "password": "hashed_pw",
    })

    store = get_store()
    now = now_iso()
    store._execute("UPDATE projects SET is_active = 0 WHERE project_id = ?", ["OFFPRJ"])
    try:
        store._execute("UPDATE projects SET source_path = ? WHERE project_id = ?", ["C:\\repo\\src", "testprj"])
    except Exception:
        pass
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
        "doc_id": "R0001",
        "project_id": "testprj",
        "type_code": "R",
        "seq": 1,
        "title": "Root Requirement",
        "group_id": "testprj-__ALL__-0001",
        "module": "__ALL__",
        "owner_id": "usr_test_001",
        "status": "open",
    })
    # 0410 T0008: a second document with an AI-provider snapshot, alongside the
    # legacy null R0001 above, so list responses can be asserted for both states.
    db_docs.create({
        "doc_id": "T0002",
        "project_id": "testprj",
        "type_code": "T",
        "seq": 2,
        "title": "AI-authored step",
        "group_id": "testprj-__ALL__-0001",
        "module": "__ALL__",
        "owner_id": "usr_test_001",
        "status": "open",
        "origin_provider_name": "Claude Sonnet 5",
        "origin_ai_run_id": "run_abc123",
    })
    yield


def _build_client():
    from modules.flow_gate.api.v1.list_routes import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _issue_bearer(tmp_path, user_id: str = "usr_test_001", project: str = "testprj") -> str:
    from modules.flow_gate.services import token_service

    with patch.object(token_service, "_scratch_dir", return_value=tmp_path / "scratch"):
        issued = token_service.issue(
            project=project,
            group_id="testprj-__ALL__-0001",
            action_scope="new",
            doc_ref=None,
            issued_to=user_id,
        )
    return issued["raw_token"]


def test_list_projects_success(seed_data, tmp_path):
    client = _build_client()
    raw = _issue_bearer(tmp_path)

    resp = client.get("/api/v1/list/projects", headers={"Authorization": f"Bearer {raw}"})

    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert any(item["project_id"] == "testprj" for item in data["items"])
    assert all(item["project_id"] != "OFFPRJ" for item in data["items"])


def test_list_projects_no_auth_401(seed_data):
    client = _build_client()

    resp = client.get("/api/v1/list/projects")

    assert resp.status_code == 401


def test_list_projects_limit_out_of_range_400(seed_data, tmp_path):
    client = _build_client()
    raw = _issue_bearer(tmp_path)

    resp = client.get("/api/v1/list/projects?limit=0", headers={"Authorization": f"Bearer {raw}"})

    assert resp.status_code == 400


def test_list_projects_limit_max_200(seed_data, tmp_path):
    client = _build_client()
    raw = _issue_bearer(tmp_path)

    resp = client.get("/api/v1/list/projects?limit=200", headers={"Authorization": f"Bearer {raw}"})

    assert resp.status_code == 200
    assert resp.json()["limit"] == 200


def test_list_projects_limit_201_400(seed_data, tmp_path):
    client = _build_client()
    raw = _issue_bearer(tmp_path)

    resp = client.get("/api/v1/list/projects?limit=201", headers={"Authorization": f"Bearer {raw}"})

    assert resp.status_code == 400


def test_list_projects_status_filter(seed_data, tmp_path):
    client = _build_client()
    raw = _issue_bearer(tmp_path)

    resp = client.get("/api/v1/list/projects?status=active", headers={"Authorization": f"Bearer {raw}"})

    assert resp.status_code == 200
    items = resp.json()["items"]
    assert any(item["project_id"] == "testprj" for item in items)
    assert all(item["project_id"] != "OFFPRJ" for item in items)


def test_list_projects_inactive_and_all_filters(seed_data, tmp_path):
    client = _build_client()
    raw = _issue_bearer(tmp_path)

    inactive = client.get("/api/v1/list/projects?is_active=inactive", headers={"Authorization": f"Bearer {raw}"})
    all_resp = client.get("/api/v1/list/projects?status=all", headers={"Authorization": f"Bearer {raw}"})

    assert inactive.status_code == 200
    assert [item["project_id"] for item in inactive.json()["items"]] == ["OFFPRJ"]
    assert all_resp.status_code == 200
    all_ids = {item["project_id"] for item in all_resp.json()["items"]}
    assert {"testprj", "OFFPRJ"}.issubset(all_ids)


def test_list_modules_success(seed_data, tmp_path):
    client = _build_client()
    raw = _issue_bearer(tmp_path)

    resp = client.get("/api/v1/list/projects/testprj/modules", headers={"Authorization": f"Bearer {raw}"})

    assert resp.status_code == 200
    assert resp.json()["items"][0]["module_id"] == "__ALL__"


def test_list_modules_404(seed_data, tmp_path):
    client = _build_client()
    raw = _issue_bearer(tmp_path)

    resp = client.get("/api/v1/list/projects/MISSING/modules", headers={"Authorization": f"Bearer {raw}"})

    assert resp.status_code == 404


def test_list_groups_success(seed_data, tmp_path):
    client = _build_client()
    raw = _issue_bearer(tmp_path)

    resp = client.get("/api/v1/list/projects/testprj/groups", headers={"Authorization": f"Bearer {raw}"})

    assert resp.status_code == 200
    assert resp.json()["items"][0]["group_id"] == "testprj-__ALL__-0001"


def test_list_groups_404(seed_data, tmp_path):
    client = _build_client()
    raw = _issue_bearer(tmp_path)

    resp = client.get("/api/v1/list/projects/MISSING/groups", headers={"Authorization": f"Bearer {raw}"})

    assert resp.status_code == 404


def test_list_documents_success(seed_data, tmp_path):
    client = _build_client()
    raw = _issue_bearer(tmp_path)

    resp = client.get("/api/v1/list/groups/testprj-__ALL__-0001/documents", headers={"Authorization": f"Bearer {raw}"})

    assert resp.status_code == 200
    assert any(item["doc_id"] == "R0001" for item in resp.json()["items"])


def test_list_documents_404(seed_data, tmp_path):
    client = _build_client()
    raw = _issue_bearer(tmp_path)

    resp = client.get("/api/v1/list/groups/MISSING/documents", headers={"Authorization": f"Bearer {raw}"})

    assert resp.status_code == 404


def test_list_documents_bad_sort_400(seed_data, tmp_path):
    client = _build_client()
    raw = _issue_bearer(tmp_path)

    resp = client.get("/api/v1/list/groups/testprj-__ALL__-0001/documents?sort=invalid", headers={"Authorization": f"Bearer {raw}"})

    assert resp.status_code == 400


def test_list_documents_offset_includes_origin_snapshot(seed_data, tmp_path):
    """0410 T0008: offset branch carries origin_provider_name/origin_ai_run_id,
    explicit null (not omitted) for the legacy R0001 row."""
    client = _build_client()
    raw = _issue_bearer(tmp_path)

    resp = client.get(
        "/api/v1/list/groups/testprj-__ALL__-0001/documents",
        headers={"Authorization": f"Bearer {raw}"},
    )

    assert resp.status_code == 200
    items = {item["doc_id"]: item for item in resp.json()["items"]}
    assert items["T0002"]["origin_provider_name"] == "Claude Sonnet 5"
    assert items["T0002"]["origin_ai_run_id"] == "run_abc123"
    assert "origin_provider_name" in items["R0001"]
    assert items["R0001"]["origin_provider_name"] is None
    assert "origin_ai_run_id" in items["R0001"]
    assert items["R0001"]["origin_ai_run_id"] is None


def test_list_documents_before_includes_origin_snapshot(seed_data, tmp_path):
    """0410 T0008: the before-cursor branch carries the same two keys as offset."""
    client = _build_client()
    raw = _issue_bearer(tmp_path)

    resp = client.get(
        "/api/v1/list/groups/testprj-__ALL__-0001/documents?before=T0002",
        headers={"Authorization": f"Bearer {raw}"},
    )

    assert resp.status_code == 200
    items = {item["doc_id"]: item for item in resp.json()["items"]}
    assert items["T0002"]["origin_provider_name"] == "Claude Sonnet 5"
    assert items["T0002"]["origin_ai_run_id"] == "run_abc123"
    assert "origin_provider_name" in items["R0001"]
    assert items["R0001"]["origin_provider_name"] is None


def test_list_doc_types_success(seed_data, tmp_path):
    client = _build_client()
    raw = _issue_bearer(tmp_path)

    resp = client.get("/api/v1/list/doc-types", headers={"Authorization": f"Bearer {raw}"})

    assert resp.status_code == 200
    assert any(item["prefix"] == "NR" for item in resp.json()["items"])


def test_search_documents_by_title(seed_data, tmp_path):
    client = _build_client()
    raw = _issue_bearer(tmp_path)

    resp = client.get(
        "/api/v1/search/documents?q=root",
        headers={"Authorization": f"Bearer {raw}"},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["query"] == "root"
    assert any(item["doc_id"] == "R0001" for item in data["items"])
    assert data["total"] >= 1


def test_search_documents_by_doc_id(seed_data, tmp_path):
    client = _build_client()
    raw = _issue_bearer(tmp_path)

    resp = client.get(
        "/api/v1/search/documents?q=r0001",
        headers={"Authorization": f"Bearer {raw}"},
    )

    assert resp.status_code == 200
    assert any(item["doc_id"] == "R0001" for item in resp.json()["items"])


def test_search_documents_case_insensitive(seed_data, tmp_path):
    client = _build_client()
    raw = _issue_bearer(tmp_path)

    resp = client.get(
        "/api/v1/search/documents?q=ROOT",
        headers={"Authorization": f"Bearer {raw}"},
    )

    assert resp.status_code == 200
    assert any(item["doc_id"] == "R0001" for item in resp.json()["items"])


def test_search_documents_no_match_empty(seed_data, tmp_path):
    client = _build_client()
    raw = _issue_bearer(tmp_path)

    resp = client.get(
        "/api/v1/search/documents?q=zzz_no_such_doc_zzz",
        headers={"Authorization": f"Bearer {raw}"},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["items"] == []
    assert data["total"] == 0


def test_search_documents_type_facet(seed_data, tmp_path):
    client = _build_client()
    raw = _issue_bearer(tmp_path)

    resp = client.get(
        "/api/v1/search/documents?q=root&type=R",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert resp.status_code == 200
    assert any(item["doc_id"] == "R0001" for item in resp.json()["items"])

    resp2 = client.get(
        "/api/v1/search/documents?q=root&type=NR",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert resp2.status_code == 200
    assert all(item["doc_id"] != "R0001" for item in resp2.json()["items"])


def test_search_documents_project_facet(seed_data, tmp_path):
    client = _build_client()
    raw = _issue_bearer(tmp_path)

    resp = client.get(
        "/api/v1/search/documents?q=root&project=testprj",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert resp.status_code == 200
    assert any(item["doc_id"] == "R0001" for item in resp.json()["items"])

    resp2 = client.get(
        "/api/v1/search/documents?q=root&project=OFFPRJ",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert resp2.status_code == 200
    assert resp2.json()["items"] == []


def test_search_documents_empty_query_400(seed_data, tmp_path):
    client = _build_client()
    raw = _issue_bearer(tmp_path)

    resp = client.get(
        "/api/v1/search/documents?q=%20%20",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert resp.status_code == 400


def test_search_documents_missing_query_400(seed_data, tmp_path):
    client = _build_client()
    raw = _issue_bearer(tmp_path)

    resp = client.get(
        "/api/v1/search/documents",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert resp.status_code == 400


def test_search_documents_no_auth_401(seed_data):
    client = _build_client()

    resp = client.get("/api/v1/search/documents?q=root")

    assert resp.status_code == 401


def test_search_documents_limit_out_of_range_400(seed_data, tmp_path):
    client = _build_client()
    raw = _issue_bearer(tmp_path)

    resp = client.get(
        "/api/v1/search/documents?q=root&limit=0",
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert resp.status_code == 400


def test_list_no_permission_403(seed_data, tmp_path):
    client = _build_client()
    raw = _issue_bearer(tmp_path)

    with patch("modules.flow_gate.services.auth_outbound.has_permission", return_value=False):
        resp = client.get("/api/v1/list/projects", headers={"Authorization": f"Bearer {raw}"})

    assert resp.status_code == 403
