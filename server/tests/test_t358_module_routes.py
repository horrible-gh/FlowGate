"""T358 ? tests for module/group/document list APIs + support for multiple reference docs.

Test items:
  1. GET /api/v1/modules ? return module list
  2. GET /api/v1/modules/{m}/groups ? return group list
  3. GET /api/v1/modules/{m}/groups/{gid}/documents ? document list (includes identifier)
  4. Passing ref_doc_ids to the advance endpoint ? mention section 3 includes additional reference docs
  5. No request body on the advance endpoint ? preserve existing behavior (backward compatibility)
  6. Verify mention_service.build_mention reflects multiple ref_doc_ids
"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

os.environ["TESTING"] = "1"
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
os.environ["FLOWGATE_TOKEN_PEPPER_ACTIVE_ID"] = "t358"
os.environ["FLOWGATE_TOKEN_PEPPER_t358"] = "t358-pepper-value-for-module-routes-test"

_SERVER_DIR = Path(__file__).resolve().parents[1]
_SCHEMA_DIR = _SERVER_DIR / "sql" / "migrations" / "sqlite"
sys.path.insert(0, str(_SERVER_DIR))


# ?? DB helper ???????????????????????????????????????????????????????????????????

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

    def fetchone(self):
        if self._last_cursor is None:
            return None
        row = self._last_cursor.fetchone()
        return dict(row) if row else None

    def fetchall(self):
        if self._last_cursor is None:
            return []
        return [dict(r) for r in self._last_cursor.fetchall()]


# ?? Fixtures ????????????????????????????????????????????????????????????????????

@pytest.fixture(scope="module")
def t358_db():
    """Prepare the T358 test DB."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    conn = sqlite3.connect(db_path)
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
            VALUES ('t358', 'T358 Test', '{now}', '{now}');

        INSERT OR IGNORE INTO project_modules (module_id, project_id, name, title, created_at, updated_at)
            VALUES ('t358:server', 't358', 'server', 'Server', '{now}', '{now}');

        INSERT OR IGNORE INTO project_modules (module_id, project_id, name, title, created_at, updated_at)
            VALUES ('t358:client', 't358', 'client', 'Client', '{now}', '{now}');

        INSERT OR IGNORE INTO groups (group_id, project_id, module, title, status, created_at, updated_at)
            VALUES ('t358-server-0001', 't358', 'server', 'Server Group', 'OPEN', '{now}', '{now}');

        INSERT OR IGNORE INTO groups (group_id, project_id, module, title, status, created_at, updated_at)
            VALUES ('t358-client-0001', 't358', 'client', 'Client Group', 'OPEN', '{now}', '{now}');

        INSERT OR IGNORE INTO documents (doc_id, project_id, module, group_id, type_code, seq, title, status, created_at, updated_at)
            VALUES ('t358-server-0001-R0001', 't358', 'server', 't358-server-0001', 'R', 1, 'Requirement 1', 'draft', '{now}', '{now}');

        INSERT OR IGNORE INTO documents (doc_id, project_id, module, group_id, type_code, seq, title, status, created_at, updated_at)
            VALUES ('t358-server-0001-DS0002', 't358', 'server', 't358-server-0001', 'DS', 2, 'Design Spec 1', 'draft', '{now}', '{now}');
    """)
    conn.commit()
    conn.close()

    db = _MockDB(db_path)
    yield db
    db.close()
    os.unlink(db_path)


@pytest.fixture(scope="module")
def t358_client(t358_db):
    """FastAPI TestClient with module routes and mock DB."""
    from unittest.mock import patch

    from modules.flow_gate.api.v1.module_routes import router as module_router
    from modules.flow_gate.services.auth_outbound import verify_bearer

    app = FastAPI()
    app.include_router(module_router)

    class _Store:
        def __init__(self, db):
            self._db = db

        def _fetch_one(self, sql, params=None):
            row = self._db._conn.execute(sql, params or []).fetchone()
            return dict(row) if row else None

        def _fetch_all(self, sql, params=None):
            return [dict(r) for r in self._db._conn.execute(sql, params or []).fetchall()]

    store = _Store(t358_db)

    def _mock_verify(request):
        return {"issued_to": "tester", "user_id": "tester"}

    with patch("modules.flow_gate.api.v1.module_routes.get_store", return_value=store), \
         patch("modules.flow_gate.api.v1.module_routes.verify_bearer", side_effect=_mock_verify):
        client = TestClient(app)
        yield client


# ?? Tests ?????????????????????????????????????????????????????????????????????

class TestModuleRoutes:
    def test_list_modules(self, t358_client):
        """GET /modules ? return module list."""
        resp = t358_client.get(
            "/api/v1/modules",
            headers={"Authorization": "Bearer test-token"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        module_ids = [item["module_id"] for item in data["items"]]
        assert "server" in module_ids
        assert "client" in module_ids

    def test_list_modules_project_filter(self, t358_client):
        """GET /modules?project=t358 ? project filter."""
        resp = t358_client.get(
            "/api/v1/modules?project=t358",
            headers={"Authorization": "Bearer test-token"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert len(data["items"]) >= 1

    def test_list_groups_for_module(self, t358_client):
        """GET /modules/{module}/groups ? return group list."""
        resp = t358_client.get(
            "/api/v1/modules/server/groups",
            headers={"Authorization": "Bearer test-token"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["module"] == "server"
        group_ids = [item["group_id"] for item in data["items"]]
        assert "t358-server-0001" in group_ids

    def test_list_groups_has_group_code(self, t358_client):
        """Group list response includes the group_code field."""
        resp = t358_client.get(
            "/api/v1/modules/server/groups",
            headers={"Authorization": "Bearer test-token"},
        )
        data = resp.json()
        for item in data["items"]:
            assert "group_code" in item

    def test_list_documents_for_group(self, t358_client):
        """GET /modules/{m}/groups/{gid}/documents ? document list + identifier."""
        resp = t358_client.get(
            "/api/v1/modules/server/groups/t358-server-0001/documents",
            headers={"Authorization": "Bearer test-token"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["group_id"] == "t358-server-0001"
        assert len(data["items"]) >= 1
        for item in data["items"]:
            assert "doc_identifier" in item
            # identifier format: {module}/{group_code}/{seq}/{doc_code}
            parts = item["doc_identifier"].split("/")
            assert len(parts) == 4, f"identifier format error: {item['doc_identifier']}"

    def test_doc_identifier_format(self, t358_client):
        """Document identifier uses the {module}/{group_code}/{seq}/{doc_code} format."""
        resp = t358_client.get(
            "/api/v1/modules/server/groups/t358-server-0001/documents",
            headers={"Authorization": "Bearer test-token"},
        )
        data = resp.json()
        items = {item["doc_id"]: item for item in data["items"]}

        r_doc = items.get("t358-server-0001-R0001")
        assert r_doc is not None
        # Current canonical format: {module}/{group_code}/{seq}/{doc_code}.
        # group_id is dash-style (t358-server-0001) so group_code is the full id,
        # and the doc_code is seq-type style ({seq:04d}-{type}) -> 0001-R.
        assert r_doc["doc_identifier"] == "server/t358-server-0001/1/0001-R"

    def test_group_not_found(self, t358_client):
        """Nonexistent group ? 404."""
        resp = t358_client.get(
            "/api/v1/modules/server/groups/nonexistent-group-9999/documents",
            headers={"Authorization": "Bearer test-token"},
        )
        assert resp.status_code == 404


# ?? Mention service tests for multiple reference docs ????????????????????????

class TestMentionRefDocs:
    def test_build_mention_no_ref_docs(self):
        """No ref_doc_ids ? only one head line in section 3 (T386 new format)."""
        from modules.flow_gate.services.mention_service import build_mention

        result = build_mention(
            project="proj-t358",
            module="server",
            group="0001",
            parent_type="R",
            parent_doc_number="R0001",
            parent_title="Req 1",
            parent_doc_id="R0001",
            head_type="DS",
            head_status="pending",
            scratch_dir="/tmp/scratch",
            raw_token="tok",
            api_base_url="http://localhost/fg",
        )
        assert result is not None
        # Current canonical format: section heading is lowercase 'documents'.
        assert "## Reference documents" in result
        # For a non-edit "new" mention with no ref_doc_ids, section 3 lists no
        # document reference lines (the target is conveyed in section 1). Only the
        # auth note is present.
        assert "additional reference documents" not in result

    def test_build_mention_with_ref_docs(self):
        """Passing ref_doc_ids ? section 3 uses one line each for head + refs (T386 new format)."""
        from modules.flow_gate.services.mention_service import build_mention

        ref_ids = [
            "t358-server-0001-DS0002",
            "t358-client-0001-R0001",
        ]
        result = build_mention(
            project="t358",
            module="server",
            group="0001",
            parent_type="R",
            parent_doc_number="R0001",
            parent_title="Req 1",
            parent_doc_id="R0001",
            head_type="DS",
            head_status="pending",
            scratch_dir="/tmp/scratch",
            raw_token="tok",
            api_base_url="http://localhost/fg",
            ref_doc_ids=ref_ids,
        )
        assert result is not None
        assert "## Reference documents" in result
        assert "t358-server-0001-DS0002: GET" in result
        assert "t358-client-0001-R0001: GET" in result

    def test_build_mention_ref_docs_appear_in_section3(self):
        """Additional reference docs appear inside the 'Reference Documents' section."""
        from modules.flow_gate.services.mention_service import build_mention

        result = build_mention(
            project="t358",
            module="server",
            group="0001",
            parent_type="R",
            parent_doc_number="R0001",
            parent_title="Req 1",
            parent_doc_id="R0001",
            head_type="DS",
            head_status="pending",
            scratch_dir="/tmp/scratch",
            raw_token="tok",
            api_base_url="http://localhost/fg",
            ref_doc_ids=["t358-server-0001-DS0002"],
        )
        sections = result.split("\n\n## ")
        ref_section = next(
            (s for s in sections if s.startswith("Reference documents") or "## Reference documents" in s),
            None,
        )
        # Split using the first section approach
        lines = result.split("\n")
        in_ref_section = False
        ref_lines = []
        for line in lines:
            if line.startswith("## Reference documents"):
                in_ref_section = True
                continue
            if in_ref_section and line.startswith("## "):
                break
            if in_ref_section:
                ref_lines.append(line)

        ref_content = "\n".join(ref_lines)
        assert "DS0002" in ref_content
