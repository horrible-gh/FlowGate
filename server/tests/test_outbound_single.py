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



    conn_mod.STORE = _PatchedStore()

    yield

    conn_mod.STORE = original_store





@pytest.fixture(scope="module")

def tmp_storage(tmp_path_factory):

    return tmp_path_factory.mktemp("single-storage")





@pytest.fixture(scope="module")

def seed_data(tmp_db, tmp_storage):

    from modules.flow_gate.db import documents as db_docs

    from modules.flow_gate.db import groups as db_groups

    from modules.flow_gate.db import projects, users

    from modules.flow_gate.db.connection import get_store, now_iso



    # Jail document content reads to this storage root so resolve_storage_path()
    # accepts the seeded file (the current contract rejects files outside the root).
    os.environ["FLOWGATE_STORAGE_DIR"] = str(tmp_storage)

    doc_rel = "testprj-__ALL__-0001-NR0002.md"

    doc_path = tmp_storage / doc_rel

    doc_path.write_text("# Phase2\n\nHello", encoding="utf-8")



    projects.create({"project_id": "testprj", "project_name": "Test Project"})

    users.create({

        "user_id": "usr_test_001",

        "username": "testuser",

        "email": "test@example.com",

        "password": "hashed_pw",

    })



    store = get_store()

    now = now_iso()

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

        [None, "R", "Requirement", "general", 1, 1, 0, now, now],

    )

    store._execute(

        "INSERT OR IGNORE INTO document_types (project_id, type_code, type_name, series, is_system, is_active, sort_order, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",

        [None, "NR", "New Request", "work", 1, 1, 1, now, now],

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

    db_docs.create({

        "doc_id": "testprj-__ALL__-0001-NR0002",

        "project_id": "testprj",

        "type_code": "NR",

        "seq": 2,

        "title": "Phase 2 Doc",

        "group_id": "testprj-__ALL__-0001",

        "module": "__ALL__",

        "owner_id": "usr_test_001",

        "file_path": doc_rel,

        "status": "open",

        "revision_no": 3,

        "triggered_by": "usr_test_001",

    })

    store._execute(

        "INSERT INTO workflow_events (event_type, project_id, group_id, document_id, actor_user_id, from_state, to_state, metadata, created_at) VALUES (?,?,?,?,?,?,?,?,?)",

        ["action_taken", "testprj", "testprj-__ALL__-0001", None, "usr_test_001", None, "open", '{"action_code":"doc_created","doc_id":"testprj-__ALL__-0001-NR0002"}', now],

    )

    # Canonical dot-style group for the group next-action endpoint, whose path
    # param is validated against the canonical group_id regex ({proj}.{module}.{seq}).
    db_groups.create({

        "group_id": "testprj.none.0001",

        "project_id": "testprj",

        "module": "none",

        "title": "Next Action Group",

    })

    db_docs.create({

        "doc_id": "testprj.none.0001.0001-R",

        "project_id": "testprj",

        "type_code": "R",

        "seq": 1,

        "title": "Root Requirement",

        "group_id": "testprj.none.0001",

        "module": "none",

        "owner_id": "usr_test_001",

    })

    db_docs.create({

        "doc_id": "testprj.none.0001.0002-NR",

        "project_id": "testprj",

        "type_code": "NR",

        "seq": 2,

        "title": "Phase 2 Doc",

        "group_id": "testprj.none.0001",

        "module": "none",

        "owner_id": "usr_test_001",

    })

    store._execute(

        "INSERT INTO workflow_events (event_type, project_id, group_id, document_id, actor_user_id, from_state, to_state, metadata, created_at) VALUES (?,?,?,?,?,?,?,?,?)",

        ["action_taken", "testprj", "testprj.none.0001", None, "usr_test_001", None, "open", '{"action_code":"doc_created","doc_id":"testprj.none.0001.0002-NR"}', now],

    )

    yield

    os.environ.pop("FLOWGATE_STORAGE_DIR", None)





def _build_client():

    from modules.flow_gate.api.v1.document_routes import router as document_router

    from modules.flow_gate.api.v1.group_routes import router as group_router

    from modules.flow_gate.api.v1.help_routes import router as help_router

    from modules.flow_gate.api.v1.project_routes import router as project_router



    app = FastAPI()

    app.include_router(help_router)

    app.include_router(document_router)

    app.include_router(project_router)

    app.include_router(group_router)

    return TestClient(app)





def _issue_bearer(tmp_path) -> str:

    from modules.flow_gate.services import token_service



    with patch.object(token_service, "_scratch_dir", return_value=tmp_path / "scratch"):

        issued = token_service.issue(

            project="testprj",

            group_id="testprj-__ALL__-0001",

            action_scope="new",

            doc_ref=None,

            issued_to="usr_test_001",

        )

    return issued["raw_token"]





def _issue_user_access_jwt() -> tuple[str, str]:

    from modules.flow_gate.auth.jwt_service import create_access_token



    return create_access_token(

        user_id="usr_test_001",

        username="tester",

        roles=["viewer"],

    )





def test_help_no_auth_200(seed_data):

    client = _build_client()



    resp = client.get("/api/v1/help")



    assert resp.status_code == 200

    assert resp.json()["ok"] is True





def test_get_document_success(seed_data, tmp_path):

    client = _build_client()

    raw = _issue_bearer(tmp_path)



    resp = client.get("/api/v1/document/testprj-__ALL__-0001-NR0002", headers={"Authorization": f"Bearer {raw}"})



    assert resp.status_code == 200

    assert resp.json()["content"] == "# Phase2\n\nHello"





def test_get_document_404(seed_data, tmp_path):

    client = _build_client()

    raw = _issue_bearer(tmp_path)



    resp = client.get("/api/v1/document/testprj-__ALL__-0001-NR9999", headers={"Authorization": f"Bearer {raw}"})



    assert resp.status_code == 404





def test_get_document_path_success(seed_data, tmp_path):

    client = _build_client()

    raw = _issue_bearer(tmp_path)



    resp = client.get("/api/v1/document/testprj-__ALL__-0001-NR0002/path", headers={"Authorization": f"Bearer {raw}"})



    assert resp.status_code == 200

    assert resp.json()["stored_path"].endswith("testprj-__ALL__-0001-NR0002.md")





def test_get_document_path_404(seed_data, tmp_path):

    client = _build_client()

    raw = _issue_bearer(tmp_path)



    resp = client.get("/api/v1/document/testprj-__ALL__-0001-NR9999/path", headers={"Authorization": f"Bearer {raw}"})



    assert resp.status_code == 404





def test_get_project_source_path_success(seed_data, tmp_path):

    client = _build_client()

    raw = _issue_bearer(tmp_path)



    resp = client.get("/api/v1/project/testprj/source-path", headers={"Authorization": f"Bearer {raw}"})



    assert resp.status_code == 200

    assert "project" in resp.json()





def test_get_project_source_path_404(seed_data, tmp_path):

    client = _build_client()

    raw = _issue_bearer(tmp_path)



    resp = client.get("/api/v1/project/missing-project/source-path", headers={"Authorization": f"Bearer {raw}"})



    assert resp.status_code == 404





def test_get_group_next_action_success(seed_data, tmp_path):

    client = _build_client()

    raw = _issue_bearer(tmp_path)



    resp = client.get("/api/v1/group/testprj.none.0001/next-action", headers={"Authorization": f"Bearer {raw}"})



    assert resp.status_code == 200

    data = resp.json()

    assert data["last_action"]["action_code"] == "doc_created"

    assert len(data["candidates"]) >= 1





def test_get_group_next_action_404(seed_data, tmp_path):

    client = _build_client()

    raw = _issue_bearer(tmp_path)



    # Canonical-format group_id that does not exist -> 404 (group not found),
    # distinct from a malformed id which would be 422.
    resp = client.get("/api/v1/group/missing.none.9999/next-action", headers={"Authorization": f"Bearer {raw}"})



    assert resp.status_code == 404





def test_single_no_auth_401(seed_data):

    client = _build_client()



    resp = client.get("/api/v1/document/DOC001")



    assert resp.status_code == 401





def test_single_no_permission_403(seed_data, tmp_path):

    client = _build_client()

    raw = _issue_bearer(tmp_path)



    with patch("modules.flow_gate.services.auth_outbound.has_permission", return_value=False):

        resp = client.get("/api/v1/document/DOC001", headers={"Authorization": f"Bearer {raw}"})



    assert resp.status_code == 403





def test_single_accepts_user_access_jwt(seed_data):

    client = _build_client()

    raw, _ = _issue_user_access_jwt()



    with patch("modules.flow_gate.services.auth_outbound.has_permission", return_value=False):

        resp = client.get(

            "/api/v1/document/testprj-__ALL__-0001-NR0002",

            headers={"Authorization": f"Bearer {raw}"},

        )



    assert resp.status_code == 200





def test_single_rejects_blacklisted_user_access_jwt(seed_data):

    client = _build_client()

    raw, jti = _issue_user_access_jwt()



    from modules.flow_gate.auth.jwt_service import decode_token

    from modules.flow_gate.auth.token_store import blacklist_token



    payload = decode_token(raw)

    blacklist_token(jti, payload["sub"], payload["exp"])



    resp = client.get(

        "/api/v1/document/testprj-__ALL__-0001-NR0002",

        headers={"Authorization": f"Bearer {raw}"},

    )



    assert resp.status_code == 401

    assert resp.json()["error_message"] == "Token has been revoked"





# ── T247 path-style regression tests ────────────────────────────────────────



@pytest.fixture(scope="module")

def seed_t247(seed_data, tmp_db):

    """T247 additional seed data for path-style tests (T254: use the project_id--doc_code format).



    doc_id format: {project_id}--{doc_code} (actual DB pattern).

    Because URL doc_no (for example, R010) is parsed as type_code='R', seq=10,

    the seq value must match the numeric portion of doc_no.

    """

    from modules.flow_gate.db import documents as db_docs

    from modules.flow_gate.db import groups as db_groups



    # Add a document for path-style lookup to the existing group (testprj-__ALL__-0001, title="Test Group")

    # doc_id = project_id--doc_code, seq=10 → URL doc_no = R010

    db_docs.create({

        "doc_id": "testprj-__ALL__-0001-R0010",

        "project_id": "testprj",

        "type_code": "R",

        "seq": 10,

        "title": "Path Style Test Doc",

        "group_id": "testprj-__ALL__-0001",

        "module": "__ALL__",

        "owner_id": "usr_test_001",

    })



    # Add a group with a Korean title

    db_groups.create({

        "group_id": "testprj-__ALL__-0002",

        "project_id": "testprj",

        "module": "__ALL__",

        "title": "Information Disclosure Request",

    })

    # doc_id = project_id--doc_code, seq=11 → URL doc_no = R011

    db_docs.create({

        "doc_id": "testprj-__ALL__-0002-R0011",

        "project_id": "testprj",

        "type_code": "R",

        "seq": 11,

        "title": "Korean Group Document",

        "group_id": "testprj-__ALL__-0002",

        "module": "__ALL__",

        "owner_id": "usr_test_001",

    })

    yield





def test_get_document_by_path_style_success(seed_t247, tmp_path):

    """Successful lookup with 4 path params (T261: direct lookup based on canonical ID)."""

    client = _build_client()

    raw = _issue_bearer(tmp_path)



    resp = client.get(

        "/api/v1/document/testprj/__ALL__/testprj-__ALL__-0001/testprj-__ALL__-0001-R0010",

        headers={"Authorization": f"Bearer {raw}"},

    )



    assert resp.status_code == 200

    data = resp.json()

    assert data["ok"] is True

    assert data["doc_id"] == "testprj-__ALL__-0001-R0010"

    assert data["title"] == "Path Style Test Doc"





def test_get_document_by_path_style_korean_group_title(seed_t247, tmp_path):

    """Successful lookup with a canonical ID that includes a Korean group_id (T261)."""

    client = _build_client()

    raw = _issue_bearer(tmp_path)



    resp = client.get(

        "/api/v1/document/testprj/__ALL__/testprj-__ALL__-0002/testprj-__ALL__-0002-R0011",

        headers={"Authorization": f"Bearer {raw}"},

    )



    assert resp.status_code == 200

    data = resp.json()

    assert data["ok"] is True

    assert data["doc_id"] == "testprj-__ALL__-0002-R0011"

    assert data["title"] == "Korean Group Document"





def test_get_document_by_path_style_not_found(seed_t247, tmp_path):

    """Returns 404 + error_message for a nonexistent canonical doc_id (T261: all cases should be document not found)."""

    client = _build_client()

    raw = _issue_bearer(tmp_path)



    # Nonexistent project (the doc_id also does not exist)

    resp = client.get(

        "/api/v1/document/nonexistent-project/__ALL__/nonexistent-__ALL__-9999/nonexistent-__ALL__-9999-R9999",

        headers={"Authorization": f"Bearer {raw}"},

    )

    assert resp.status_code == 404

    assert "document not found" in resp.json()["error_message"]



    # Nonexistent group (the doc_id also does not exist)

    resp = client.get(

        "/api/v1/document/testprj/__ALL__/testprj-__ALL__-9999/testprj-__ALL__-9999-R9999",

        headers={"Authorization": f"Bearer {raw}"},

    )

    assert resp.status_code == 404

    assert "document not found" in resp.json()["error_message"]



    # Nonexistent document

    resp = client.get(

        "/api/v1/document/testprj/__ALL__/testprj-__ALL__-0001/testprj-__ALL__-0001-R9999",

        headers={"Authorization": f"Bearer {raw}"},

    )

    assert resp.status_code == 404

    assert "document not found" in resp.json()["error_message"]





def test_get_document_by_path_style_invalid_module(seed_t247, tmp_path):

    """Returns 400 when the module is not __ALL__."""

    client = _build_client()

    raw = _issue_bearer(tmp_path)



    resp = client.get(

        "/api/v1/document/testprj/backend/testprj-__ALL__-0001/testprj-__ALL__-0001-R0001",

        headers={"Authorization": f"Bearer {raw}"},

    )



    assert resp.status_code == 400

    assert "error_message" in resp.json()
