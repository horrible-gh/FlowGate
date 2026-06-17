"""T505 ? backend integration tests for module creation.

Test items:
  1. POST /api/v1/projects/{project_id}/modules ? 201 + module record created
  2. Duplicate module creation ? 409
  3. Nonexistent project_id ? 404
  4. Missing name ? 400
  5. get_group_tree includes a project_modules module node
"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

os.environ["TESTING"] = "1"
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
os.environ["FLOWGATE_TOKEN_PEPPER_ACTIVE_ID"] = "t505"
os.environ["FLOWGATE_TOKEN_PEPPER_t505"] = "t505-pepper-for-module-create-test"

_SERVER_DIR = Path(__file__).resolve().parents[1]
_SCHEMA_DIR = _SERVER_DIR / "sql" / "migrations" / "sqlite"
sys.path.insert(0, str(_SERVER_DIR))


# ?? DB fixture ??????????????????????????????????????????????????????????????????

class _Store:
    """Mock store for TestClient ? replaces module_routes.get_store()."""
    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def _fetch_one(self, sql: str, params=None):
        row = self._conn.execute(sql, params or []).fetchone()
        return dict(row) if row else None

    def _fetch_all(self, sql: str, params=None):
        return [dict(r) for r in self._conn.execute(sql, params or []).fetchall()]

    def _execute(self, sql: str, params=None):
        self._conn.execute(sql, params or [])
        self._conn.commit()


@pytest.fixture(scope="module")
def t505_conn():
    """Test DB with all migrations applied."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = OFF")

    for migration_file in sorted(_SCHEMA_DIR.glob("*.sql")):
        try:
            conn.executescript(migration_file.read_text(encoding="utf-8"))
        except Exception:
            pass

    now = datetime.now(timezone.utc).isoformat()
    conn.executescript(f"""
        INSERT OR IGNORE INTO projects (project_id, project_name, created_at, updated_at)
            VALUES ('t505proj', 'T505 Test Project', '{now}', '{now}');
    """)
    conn.commit()

    yield conn
    conn.close()
    os.unlink(db_path)


@pytest.fixture(scope="module")
def t505_client(t505_conn):
    from unittest.mock import patch

    from modules.flow_gate.api.v1.module_routes import router as module_router
    from modules.flow_gate.services.auth_outbound import verify_bearer

    app = FastAPI()
    app.include_router(module_router)

    store = _Store(t505_conn)

    def _mock_verify(request):
        return {"issued_to": "tester", "user_id": "tester"}

    with patch("modules.flow_gate.api.v1.module_routes.get_store", return_value=store), \
         patch("modules.flow_gate.api.v1.module_routes.verify_bearer", side_effect=_mock_verify):
        yield TestClient(app, raise_server_exceptions=True)


# ── POST /api/v1/projects/{project_id}/modules テスト ─────────────────────────

class TestCreateModule:
    def test_create_module_success(self, t505_client, t505_conn):
        """Module creation succeeds ? 201 + ok:true."""
        resp = t505_client.post(
            "/api/v1/projects/t505proj/modules",
            json={"name": "server", "title": "Server Module"},
            headers={"Authorization": "Bearer test-token"},
        )
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert data["ok"] is True
        assert data["name"] == "server"
        assert data["title"] == "Server Module"
        assert data["module_id"] == "t505proj:server"
        assert data["project_id"] == "t505proj"

        # DB verification
        row = t505_conn.execute(
            "SELECT * FROM project_modules WHERE module_id = 't505proj:server'"
        ).fetchone()
        assert row is not None
        assert row["name"] == "server"

    def test_create_module_title_defaults_to_name(self, t505_client):
        """If title is omitted, store the same value as name."""
        resp = t505_client.post(
            "/api/v1/projects/t505proj/modules",
            json={"name": "no-title-module"},
            headers={"Authorization": "Bearer test-token"},
        )
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert data["title"] == "no-title-module"

    def test_create_module_duplicate_409(self, t505_client):
        """Return 409 when the same project+name already exists."""
        # 'server' was already created in test_create_module_success
        resp = t505_client.post(
            "/api/v1/projects/t505proj/modules",
            json={"name": "server"},
            headers={"Authorization": "Bearer test-token"},
        )
        assert resp.status_code == 409, resp.text
        data = resp.json()
        assert data["ok"] is False

    def test_create_module_project_not_found(self, t505_client):
        """Nonexistent project_id ? 404."""
        resp = t505_client.post(
            "/api/v1/projects/nonexistent-proj-9999/modules",
            json={"name": "anything"},
            headers={"Authorization": "Bearer test-token"},
        )
        assert resp.status_code == 404, resp.text
        data = resp.json()
        assert data["ok"] is False

    def test_create_module_empty_name(self, t505_client):
        """Blank/empty name ? 400."""
        resp = t505_client.post(
            "/api/v1/projects/t505proj/modules",
            json={"name": "   "},
            headers={"Authorization": "Bearer test-token"},
        )
        assert resp.status_code == 400, resp.text
        data = resp.json()
        assert data["ok"] is False


# ?? Verify get_group_tree includes project_modules ???????????????????????????

class TestGroupTreeIncludesProjectModules:
    def test_module_node_from_project_modules(self, t505_conn):
        """project_modules に登録済みモジュールがtreeに現れる (グループ不要)."""
        from unittest.mock import patch

        now = datetime.now(timezone.utc).isoformat()
        # orphan-mod: グループなし、project_modulesのみ
        t505_conn.execute(
            "INSERT OR IGNORE INTO project_modules "
            "(module_id, project_id, name, title, created_at, updated_at) "
            "VALUES ('t505proj:orphan-mod', 't505proj', 'orphan-mod', 'Orphan Module', ?, ?)",
            (now, now),
        )
        t505_conn.commit()

        from modules.flow_gate.process_service import get_group_tree

        from modules.flow_gate.db import connection as _conn

        # With store.py retired, all get_group_tree DB access (get_project_modules,
        # get_groups_by_projects, get_docs_for_tree_by_group, and
        # get_orphan_docs_for_tree) goes through get_store(). Replacing
        # connection.STORE with a store connected to t505_conn is sufficient.
        # The groups/documents tables are empty, so module nodes come only from project_modules (orphan-mod).
        with patch.object(_conn, "STORE", _Store(t505_conn)):
            result = get_group_tree("t505proj")

        node_types = {n["node_type"] for n in result["nodes"]}
        assert "module" in node_types, "module ノードが含まれていない"

        module_labels = [
            n["label"]
            for n in result["nodes"]
            if n["node_type"] == "module"
        ]
        assert "orphan-mod" in module_labels, \
            f"orphan-mod がtreeに見つからない: {module_labels}"
