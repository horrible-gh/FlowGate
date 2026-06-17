"""T330 validation ? verify the token/issue mention response (sequence + token).

Uses the TS021 test structure.
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
os.environ["FLOWGATE_TOKEN_PEPPER_ACTIVE_ID"] = "t330"
os.environ["FLOWGATE_TOKEN_PEPPER_t330"] = "t330-pepper-for-mention-verification"

_SERVER_DIR = Path(__file__).resolve().parents[1]
_SCHEMA_DIR = _SERVER_DIR / "sql" / "migrations" / "sqlite"
sys.path.insert(0, str(_SERVER_DIR))


class _TestDB:
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

    def raw_execute(self, sql: str):
        self._conn.executescript(sql)
        self._conn.commit()

    def close(self):
        self._conn.close()


@pytest.fixture(scope="module")
def t330_storage(tmp_path_factory):
    """T330-specific storage."""
    d = tmp_path_factory.mktemp("t330_storage")
    os.environ["FLOWGATE_STORAGE_DIR"] = str(d)
    yield d
    os.environ.pop("FLOWGATE_STORAGE_DIR", None)


@pytest.fixture(scope="module")
def t330_db(t330_storage):
    """T330-specific test DB."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    db = _TestDB(db_path)

    # Apply migrations
    for sql_file in sorted(_SCHEMA_DIR.glob("*.sql")):
        try:
            db.raw_execute(sql_file.read_text(encoding="utf-8"))
        except Exception:
            pass

    now = datetime.now(timezone.utc).isoformat()

    # Project
    db.execute(
        "INSERT OR IGNORE INTO projects (project_id, project_name, is_active, created_at, updated_at) "
        "VALUES (?, ?, 1, ?, ?)",
        ["proj-t330", "T330Project", now, now],
    )

    # User
    db.execute(
        "INSERT OR IGNORE INTO users "
        "(user_id, username, email, password, is_active, is_admin, first_login_required, created_at, updated_at) "
        "VALUES (?, ?, ?, 'hashed_pw', 1, 0, 0, ?, ?)",
        ["usr_t330_worker", "t330_worker", "worker@t330.test", now, now],
    )

    # Assign role
    db.execute(
        "INSERT OR IGNORE INTO user_project_roles (user_id, project_id, role_id, granted_at) "
        "VALUES (?, ?, ?, ?)",
        ["usr_t330_worker", "proj-t330", "role_worker", now],
    )

    # Groups (three cases)
    for group_seq in ["0001", "0002", "0003"]:
        db.execute(
            "INSERT OR IGNORE INTO groups "
            "(group_id, project_id, module, title, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                f"proj-t330-server-{group_seq}",
                "proj-t330",
                "server",
                f"TestGroup{group_seq}",
                "OPEN",
                now,
                now,
            ],
        )

    # Documents
    docs = [
        ("proj-t330-server-0001-R0001", "proj-t330-server-0001", "R", 1),
        ("proj-t330-server-0002-D0001", "proj-t330-server-0002", "D", 1),
        ("proj-t330-server-0003-DS0001", "proj-t330-server-0003", "DS", 1),
    ]

    for doc_id, group_id, type_code, seq in docs:
        db.execute(
            "INSERT OR IGNORE INTO documents "
            "(doc_id, project_id, module, group_id, type_code, seq, title, file_path, status, owner_id, created_at, updated_at, revision_no) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [doc_id, "proj-t330", "server", group_id, type_code, seq, "Doc", "", "open", "usr_t330_worker", now, now, 0],
        )

    # Workflow sequences
    # Case 1: pending
    db.execute(
        "INSERT OR IGNORE INTO workflow_sequences (doc_id, created_at, updated_at) "
        "VALUES (?, ?, ?)",
        ["proj-t330-server-0001-R0001", now, now],
    )
    seq1 = db.fetch_one("SELECT id FROM workflow_sequences WHERE doc_id = ?", ["proj-t330-server-0001-R0001"])
    if seq1:
        db.execute(
            "INSERT OR IGNORE INTO workflow_sequence_items "
            "(sequence_id, item_seq, type, label, doc_class, sort_order, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [seq1["id"], 1, "D", "Design", "R", 0, "pending", now, now],
        )

    # Case 3: in_progress
    db.execute(
        "INSERT OR IGNORE INTO workflow_sequences (doc_id, created_at, updated_at) "
        "VALUES (?, ?, ?)",
        ["proj-t330-server-0003-DS0001", now, now],
    )
    seq3 = db.fetch_one("SELECT id FROM workflow_sequences WHERE doc_id = ?", ["proj-t330-server-0003-DS0001"])
    if seq3:
        db.execute(
            "INSERT OR IGNORE INTO workflow_sequence_items "
            "(sequence_id, item_seq, type, label, doc_class, sort_order, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [seq3["id"], 1, "D", "Design", "R", 0, "in_progress", now, now],
        )

    yield db
    db.close()
    os.unlink(db_path)


@pytest.fixture(scope="module", autouse=True)
def t330_store(t330_db):
    """Replace the real DB store."""
    from modules.flow_gate.db import connection as conn_mod
    from modules.flow_gate.rbac import permission_service

    class _TestStore(conn_mod.FlowGateStore):
        def __init__(self):
            self._db = t330_db
            self._sq = None

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
def t330_client(t330_app):
    """T330 TestClient."""
    return TestClient(t330_app, raise_server_exceptions=True)


def _make_jwt(user_id: str) -> str:
    """Create a real JWT."""
    from modules.flow_gate.auth.jwt_service import create_access_token

    token, _ = create_access_token(user_id=user_id, username=user_id, roles=[])
    return token


def _auth_header(user_id: str) -> dict:
    return {"Authorization": f"Bearer {_make_jwt(user_id)}"}


class TestT330MentionVerification:
    """T330 validation tests."""

    def test_case1_seq_head_pending(self, t330_client):
        """Case 1: Sequence determined (pending) ? next_type='D'."""
        resp = t330_client.post(
            "/api/v1/token/issue",
            json={
                "project": "proj-t330",
                "group": "0001", "module": "server",
                "action_scope": "new",
                "doc_ref": "proj-t330-server-0001-R0001",
            },
            headers=_auth_header("usr_t330_worker"),
        )
        assert resp.status_code == 200, f"FAIL: {resp.status_code} {resp.text}"
        body = resp.json()
        mention = body["mention"]
        raw_token = body["raw_token"]

        # Check next_type='D'
        for line in mention.split("\n"):
            if line.startswith("next_type:"):
                actual = line.split("next_type:")[1].split("#")[0].strip()
                assert actual == "D", f"expected D, got {actual}"
                break
        else:
            assert False, "next_type line missing"

        # Check raw_token
        assert f"Bearer {raw_token}" in mention, f"raw_token not included: {mention}"

    def test_case2_no_sequence(self, t330_client):
        """Case 2: Sequence unresolved ? next_type='<??? ???>'."""
        resp = t330_client.post(
            "/api/v1/token/issue",
            json={
                "project": "proj-t330",
                "group": "0002", "module": "server",
                "action_scope": "new",
                "doc_ref": "proj-t330-server-0002-D0001",
            },
            headers=_auth_header("usr_t330_worker"),
        )
        assert resp.status_code == 200, f"FAIL: {resp.status_code} {resp.text}"
        body = resp.json()
        mention = body["mention"]
        raw_token = body["raw_token"]

        # Check next_type='<??? ???>'
        for line in mention.split("\n"):
            if line.startswith("next_type:"):
                actual = line.split("next_type:")[1].split("#")[0].strip()
                assert actual == "<Sequence unresolved>", f"expected <Sequence unresolved>, got {actual}"
                break
        else:
            assert False, "next_type line missing"

        # Check raw_token
        assert f"Bearer {raw_token}" in mention, f"raw_token not included"

    def test_case3_seq_head_in_progress(self, t330_client):
        """Case 3: Sequence in progress (in_progress) ? next_type='<?? ?: D>'."""
        resp = t330_client.post(
            "/api/v1/token/issue",
            json={
                "project": "proj-t330",
                "group": "0003", "module": "server",
                "action_scope": "new",
                "doc_ref": "proj-t330-server-0003-DS0001",
            },
            headers=_auth_header("usr_t330_worker"),
        )
        assert resp.status_code == 200, f"FAIL: {resp.status_code} {resp.text}"
        body = resp.json()
        mention = body["mention"]
        raw_token = body["raw_token"]

        # Check next_type='<?? ?: D>'
        for line in mention.split("\n"):
            if line.startswith("next_type:"):
                actual = line.split("next_type:")[1].split("#")[0].strip()
                assert actual == "<In progress: D>", f"expected <In progress: D>, got {actual}"
                break
        else:
            assert False, "next_type line missing"

        # Check raw_token
        assert f"Bearer {raw_token}" in mention, f"raw_token not included"

