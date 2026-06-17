"""T330 review — token/issue response mention (sequence consistency + real token).

Integration test using the TS021 test structure.
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

os.environ["TESTING"] = "1"
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
os.environ["FLOWGATE_TOKEN_PEPPER_ACTIVE_ID"] = "t330"
os.environ["FLOWGATE_TOKEN_PEPPER_t330"] = "t330-pepper-for-mention-verification"

_SERVER_DIR = Path(__file__).resolve().parents[1]
_SCHEMA_DIR = _SERVER_DIR / "sql" / "migrations" / "sqlite"
sys.path.insert(0, str(_SERVER_DIR))


class _TestDB:
    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql: str, params=None):
        self._conn.execute(sql, params or [])
        self._conn.commit()
        return self._conn

    def fetch_one(self, sql: str, params=None):
        row = self._conn.execute(sql, params or []).fetchone()
        return dict(row) if row else None

    def commit(self):
        self._conn.commit()

    def close(self):
        self._conn.close()


@pytest.fixture(scope="module")
def t330_db(all_migrations_db):
    """Prepare the T330 test DB."""
    db = _TestDB(all_migrations_db)
    now = datetime.now(timezone.utc).isoformat()

    # Project
    db.execute(
        "INSERT OR IGNORE INTO projects (project_id, project_name, created_at, updated_at) "
        "VALUES (?, ?, ?, ?)",
        ["proj-t330", "T330 Test", now, now],
    )

    # Group
    for group_seq in ["0001", "0002", "0003"]:
        db.execute(
            "INSERT OR IGNORE INTO groups "
            "(group_id, project_id, module, title, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                f"proj-t330.server.{group_seq}",
                "proj-t330",
                "server",
                f"Group {group_seq}",
                "OPEN",
                now,
                now,
            ],
        )

    # User
    db.execute(
        "INSERT OR IGNORE INTO users "
        "(user_id, username, email, password, is_active, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ["usr_t330", "t330_worker", "t330@test", "hashed", 1, now, now],
    )

    # Role assignment
    db.execute(
        "INSERT OR IGNORE INTO user_project_roles (user_id, project_id, role_id, granted_at) "
        "VALUES (?, ?, ?, ?)",
        ["usr_t330", "proj-t330", "role_worker", now],
    )

    # Documents
    docs = [
        ("proj-t330.server.0001.0001-R", "proj-t330.server.0001", "R", "1"),
        ("proj-t330.server.0002.0001-D", "proj-t330.server.0002", "D", "1"),
        ("proj-t330.server.0003.0001-DS", "proj-t330.server.0003", "DS", "1"),
        ("proj-t330.server.0003.0002-D", "proj-t330.server.0003", "D", "2"),
    ]

    for doc_id, group_id, type_code, seq in docs:
        db.execute(
            "INSERT OR IGNORE INTO documents "
            "(doc_id, project_id, module, group_id, type_code, seq, title, status, owner_id, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [doc_id, "proj-t330", "server", group_id, type_code, seq, "Test Doc", "open", "usr_t330", now, now],
        )

    # Workflow sequence + head
    # Case 1: pending
    db.execute(
        "INSERT OR IGNORE INTO workflow_sequences (doc_id, created_at, updated_at) "
        "VALUES (?, ?, ?)",
        ["proj-t330.server.0001.0001-R", now, now],
    )
    seq1 = db.fetch_one("SELECT id FROM workflow_sequences WHERE doc_id = ?", ["proj-t330.server.0001.0001-R"])
    if seq1:
        db.execute(
            "INSERT OR IGNORE INTO workflow_sequence_items "
            "(sequence_id, item_seq, type, label, doc_class, sort_order, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [seq1["id"], 1, "D", "Design", "R", 0, now, now],
        )

    # Case 3: in_progress
    db.execute(
        "INSERT OR IGNORE INTO workflow_sequences (doc_id, created_at, updated_at) "
        "VALUES (?, ?, ?)",
        ["proj-t330.server.0003.0001-DS", now, now],
    )
    seq3 = db.fetch_one("SELECT id FROM workflow_sequences WHERE doc_id = ?", ["proj-t330.server.0003.0001-DS"])
    if seq3:
        db.execute(
            "INSERT OR IGNORE INTO workflow_sequence_items "
            "(sequence_id, item_seq, type, label, doc_class, sort_order, result_doc_id, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [seq3["id"], 1, "D", "Design", "R", 0, "proj-t330.server.0003.0002-D", now, now],
        )

    db.commit()
    yield db


@pytest.fixture(scope="module")
def t330_store(t330_db):
    """Swap in the real DB store."""
    from modules.flow_gate.db import connection as conn_mod
    from modules.flow_gate.rbac import permission_service

    class _TestStore(conn_mod.FlowGateStore):
        def __init__(self):
            self._db = t330_db._conn

    original_store = conn_mod.STORE
    conn_mod.STORE = _TestStore()

    with permission_service._cache_lock:
        permission_service._cache.clear()

    yield

    conn_mod.STORE = original_store
    with permission_service._cache_lock:
        permission_service._cache.clear()


@pytest.fixture(scope="module")
def t330_app():
    """T330 app."""
    from modules.flow_gate.api.token_routes import router as token_router
    from modules.flow_gate.api.v1.help_routes import router as help_router

    app = FastAPI()
    app.include_router(token_router)
    app.include_router(help_router)
    return app


@pytest.fixture(scope="module")
def t330_client(t330_app, t330_store):
    """T330 TestClient."""
    return TestClient(t330_app, raise_server_exceptions=True)


def make_jwt(user_id: str) -> str:
    """Create a real JWT."""
    from modules.flow_gate.auth.jwt_service import create_access_token

    token, _ = create_access_token(user_id=user_id, username=user_id, roles=["role_worker"])
    return token


class TestT330:
    """T330 review."""

    def test_case1_seq_head_pending(self, t330_client):
        """Case 1: sequence decided (pending) -> next_type='D'."""
        jwt = make_jwt("usr_t330")
        resp = t330_client.post(
            "/api/v1/token/issue",
            json={
                "project": "proj-t330",
                "group": "0001", "module": "server",
                "action_scope": "new",
                "doc_ref": "proj-t330.server.0001.0001-R",
            },
            headers={"Authorization": f"Bearer {jwt}"},
        )
        assert resp.status_code == 200, f"HTTP {resp.status_code}: {resp.text}"
        body = resp.json()
        mention = body["mention"]
        raw_token = body["raw_token"]

        # Check next_type
        for line in mention.split("\n"):
            if line.startswith("next_type:"):
                actual = line.split("next_type:")[1].split("#")[0].strip()
                assert actual == "D", f"expected D, got {actual}"
                break
        else:
            raise AssertionError("next_type line missing")

        # Check Authorization token match
        for line in mention.split("\n"):
            if "Authorization:" in line:
                assert f"Bearer {raw_token}" in line, f"raw_token mismatch: {line}"
                break
        else:
            raise AssertionError("Authorization line missing")

    def test_case2_no_sequence(self, t330_client):
        """Case 2: sequence undecided -> next_type='<sequence undecided>'."""
        jwt = make_jwt("usr_t330")
        resp = t330_client.post(
            "/api/v1/token/issue",
            json={
                "project": "proj-t330",
                "group": "0002", "module": "server",
                "action_scope": "new",
                "doc_ref": "proj-t330.server.0002.0001-D",
            },
            headers={"Authorization": f"Bearer {jwt}"},
        )
        assert resp.status_code == 200, f"HTTP {resp.status_code}: {resp.text}"
        body = resp.json()
        mention = body["mention"]
        raw_token = body["raw_token"]

        # Check next_type
        for line in mention.split("\n"):
            if line.startswith("next_type:"):
                actual = line.split("next_type:")[1].split("#")[0].strip()
                assert actual == "<Sequence undecided>", f"expected <Sequence undecided>, got {actual}"
                break
        else:
            raise AssertionError("next_type line missing")

        # Check Authorization token match
        for line in mention.split("\n"):
            if "Authorization:" in line:
                assert f"Bearer {raw_token}" in line, f"raw_token mismatch: {line}"
                break
        else:
            raise AssertionError("Authorization line missing")

    def test_case3_seq_head_in_progress(self, t330_client):
        """Case 3: sequence in progress -> next_type='<in progress: D>'."""
        jwt = make_jwt("usr_t330")
        resp = t330_client.post(
            "/api/v1/token/issue",
            json={
                "project": "proj-t330",
                "group": "0003", "module": "server",
                "action_scope": "new",
                "doc_ref": "proj-t330.server.0003.0001-DS",
            },
            headers={"Authorization": f"Bearer {jwt}"},
        )
        assert resp.status_code == 200, f"HTTP {resp.status_code}: {resp.text}"
        body = resp.json()
        mention = body["mention"]
        raw_token = body["raw_token"]

        # Check next_type
        for line in mention.split("\n"):
            if line.startswith("next_type:"):
                actual = line.split("next_type:")[1].split("#")[0].strip()
                assert actual == "<In progress: D>", f"expected <In progress: D>, got {actual}"
                break
        else:
            raise AssertionError("next_type line missing")

        # Check Authorization token match
        for line in mention.split("\n"):
            if "Authorization:" in line:
                assert f"Bearer {raw_token}" in line, f"raw_token mismatch: {line}"
                break
        else:
            raise AssertionError("Authorization line missing")
