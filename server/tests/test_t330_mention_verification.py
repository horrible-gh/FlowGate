"""T330 — the mention returned by POST /token/issue agrees with the workflow sequence.

Three cases, driven through the endpoint with a real issued token:
  1. sequence decided, head pending      → next_type is the head type ('D')
  2. no sequence on the document         → next_type is the "undecided" placeholder
  3. sequence decided, head in progress  → next_type marks the head as already running

flowgate.default.0394 T0004 (NR0003 §7.1 / §13-14) — this file is the survivor of four.

T330 left `test_t330_direct_mention.py`, `test_t330_mention_integration.py`,
`test_t330_mention_verification.py` and `test_t330_verify_mention.py` side by side, about
39 KB in total, and nothing in the four names said what distinguished them. Three of them
— integration, verification, verify_mention — turned out to be the same three cases
against the same endpoint, asserting the same two things (the next_type line, and that the
mention echoes the token it was issued with); they differed only in wording and in each
carrying its own private DB double. This one was kept because its assertions are the
strictest of the three (it checks that the mention and the token exist at all, and reads
the Authorization line specifically rather than searching the whole text), and the other
two were deleted. No case was lost: every assertion the deleted files made is made here.

`test_t330_direct_mention.py` still stands and is NOT a duplicate — it calls the mention
builder directly, with no HTTP and no token, so it fails differently (a builder bug rather
than a wiring bug) and is the faster of the two ways to find the same defect. The pair is
deliberate: one unit, one through the endpoint.
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

# ?? Environment variables (set before import) ????????????????????????????????
os.environ["TESTING"] = "1"
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
os.environ["FLOWGATE_TOKEN_PEPPER_ACTIVE_ID"] = "t330"
os.environ["FLOWGATE_TOKEN_PEPPER_t330"] = "t330-pepper-value-for-mention-verification-test"

_SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SERVER_DIR))


class _TestStore:
    """Test store wrapper ? replace with the real TestDB."""

    def __init__(self, test_db):
        self._db = test_db
        self._sq = None

    def _fetch_one(self, sql: str, params=None):
        row = self._db.execute(sql, params or []).fetchone()
        return dict(row) if row else None

    def _fetch_all(self, sql: str, params=None):
        return [dict(r) for r in self._db.execute(sql, params or []).fetchall()]

    def _execute(self, sql: str, params=None):
        self._db.execute(sql, params or [])
        self._db.commit()

    def _sql(self, key: str) -> str:
        from modules.flow_gate.db.connection import FlowGateStore
        return FlowGateStore._sql(self, key)


@pytest.fixture(scope="module", autouse=True)
def t330_db(all_migrations_db):
    """Prepare the T330 test DB (existing migrations + seed data)."""
    db = all_migrations_db
    now = datetime.now(timezone.utc).isoformat()

    # Create project
    db.execute(
        "INSERT OR IGNORE INTO projects (project_id, project_name, created_at, updated_at) "
        "VALUES (?, ?, ?, ?)",
        ["proj-t330", "T330 Test Project", now, now],
    )

    # Create groups (0001 ? seq pending, 0002 ? no seq, 0003 ? seq in_progress)
    for group_seq in ["0001", "0002", "0003"]:
        db.execute(
            "INSERT OR IGNORE INTO groups "
            "(group_id, project_id, module, title, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                f"proj-t330.server.{group_seq}",
                "proj-t330",
                "server",
                f"Test Group {group_seq}",
                "OPEN",
                now,
                now,
            ],
        )

    # Create user
    db.execute(
        "INSERT OR IGNORE INTO users "
        "(user_id, username, email, password, is_active, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ["usr_t330_worker", "worker_t330", "worker@t330.test", "hashed", 1, now, now],
    )

    # Assign role
    db.execute(
        "INSERT OR IGNORE INTO user_project_roles (user_id, project_id, role_id, granted_at) "
        "VALUES (?, ?, ?, ?)",
        ["usr_t330_worker", "proj-t330", "role_worker", now],
    )

    # Create documents
    docs = [
        {
            "doc_id": "proj-t330.server.0001.0001-R",
            "group_id": "proj-t330.server.0001",
            "type_code": "R",
            "seq": 1,
            "title": "Case1: Head Pending",
        },
        {
            "doc_id": "proj-t330.server.0002.0001-D",
            "group_id": "proj-t330.server.0002",
            "type_code": "D",
            "seq": 1,
            "title": "Case2: No Sequence",
        },
        {
            "doc_id": "proj-t330.server.0003.0001-DS",
            "group_id": "proj-t330.server.0003",
            "type_code": "DS",
            "seq": 1,
            "title": "Case3: Head In Progress",
        },
        {
            "doc_id": "proj-t330.server.0003.0002-D",
            "group_id": "proj-t330.server.0003",
            "type_code": "D",
            "seq": 2,
            "title": "Case3: In Progress Result",
        },
    ]

    for doc in docs:
        db.execute(
            "INSERT OR IGNORE INTO documents "
            "(doc_id, project_id, module, group_id, type_code, seq, title, "
            " status, owner_id, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                doc["doc_id"], "proj-t330", "server", doc["group_id"],
                doc["type_code"], doc["seq"], doc["title"],
                "open", "usr_t330_worker", now, now,
            ],
        )

    # Case 1: workflow sequence + head (pending)
    db.execute(
        "INSERT OR IGNORE INTO workflow_sequences "
        "(doc_id, created_at, updated_at) "
        "VALUES (?, ?, ?)",
        ["proj-t330.server.0001.0001-R", now, now],
    )
    # Get workflow_sequences sequence ID
    seq1_id = db.execute(
        "SELECT id FROM workflow_sequences WHERE doc_id = ?",
        ["proj-t330.server.0001.0001-R"],
    ).fetchone()[0]
    
    db.execute(
        "INSERT OR IGNORE INTO workflow_sequence_items "
        "(sequence_id, item_seq, type, label, doc_class, sort_order, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [seq1_id, 1, "D", "Design", "R", 0, now, now],
    )

    # Case 3: workflow sequence + head (in_progress)
    db.execute(
        "INSERT OR IGNORE INTO workflow_sequences "
        "(doc_id, created_at, updated_at) "
        "VALUES (?, ?, ?)",
        ["proj-t330.server.0003.0001-DS", now, now],
    )
    # Get workflow_sequences sequence ID
    seq3_id = db.execute(
        "SELECT id FROM workflow_sequences WHERE doc_id = ?",
        ["proj-t330.server.0003.0001-DS"],
    ).fetchone()[0]
    
    db.execute(
        "INSERT OR IGNORE INTO workflow_sequence_items "
        "(sequence_id, item_seq, type, label, doc_class, sort_order, result_doc_id, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [seq3_id, 1, "D", "Design", "R", 0, "proj-t330.server.0003.0002-D", now, now],
    )

    db.commit()
    yield db


@pytest.fixture(scope="module")
def t330_store(t330_db):
    """Replace the store backed by the real DB."""
    from modules.flow_gate.db import connection as conn_mod
    from modules.flow_gate.rbac import permission_service

    original_store = conn_mod.STORE
    conn_mod.STORE = _TestStore(t330_db)

    with permission_service._cache_lock:
        permission_service._cache.clear()

    yield conn_mod.STORE

    conn_mod.STORE = original_store
    with permission_service._cache_lock:
        permission_service._cache.clear()


@pytest.fixture(scope="module")
def t330_app():
    """T330-specific test app."""
    from modules.flow_gate.api.token_routes import router as token_router
    from modules.flow_gate.api.v1.help_routes import router as help_router

    app = FastAPI()
    app.include_router(token_router)
    app.include_router(help_router)
    return app


@pytest.fixture(scope="module")
def t330_client(t330_app, t330_store):
    """TestClient ? real HTTP calls."""
    return TestClient(t330_app, raise_server_exceptions=True)


def make_jwt(user_id: str, roles: list[str] | None = None) -> str:
    """Create a real JWT."""
    from modules.flow_gate.auth.jwt_service import create_access_token

    token, _ = create_access_token(
        user_id=user_id,
        username=user_id,
        roles=roles or ["role_worker"],
    )
    return token


def auth_header(user_id: str, roles: list[str] | None = None) -> dict:
    return {"Authorization": f"Bearer {make_jwt(user_id, roles)}"}


class TestT330MentionVerification:
    """T330 validation ? verify token/issue mention response."""

    def test_case1_seq_head_pending(self, t330_client):
        """Case 1: Sequence determined (head status=pending) ? next_type='D'."""
        resp = t330_client.post(
            "/api/v1/token/issue",
            json={
                "project": "proj-t330",
                "group": "0001", "module": "server",
                "action_scope": "new",
                "doc_ref": "proj-t330.server.0001.0001-R",
            },
            headers=auth_header("usr_t330_worker"),
        )

        assert resp.status_code == 200, f"HTTP {resp.status_code}: {resp.text}"
        body = resp.json()
        mention = body.get("mention")
        raw_token = body.get("raw_token")

        assert mention, "mention missing"
        assert raw_token, "raw_token missing"

        # Validation 1: check next_type
        next_type_line = None
        for line in mention.split("\n"):
            if line.startswith("next_type:"):
                next_type_line = line
                break

        assert next_type_line, "next_type line missing"
        actual_next_type = next_type_line.split("next_type:")[1].split("#")[0].strip()
        assert actual_next_type == "D", f"expected 'D', actual '{actual_next_type}'"

        # Validation 2: Authorization token matches
        auth_line = None
        for line in mention.split("\n"):
            if "Authorization:" in line:
                auth_line = line
                break

        assert auth_line, "Authorization line missing"
        assert f"Bearer {raw_token}" in auth_line, (
            f"raw_token mismatch: raw_token={raw_token}, auth_line={auth_line}"
        )

    def test_case2_no_sequence(self, t330_client):
        """Case 2: Sequence unresolved (no seq) ? next_type='<??? ???>'."""
        resp = t330_client.post(
            "/api/v1/token/issue",
            json={
                "project": "proj-t330",
                "group": "0002", "module": "server",
                "action_scope": "new",
                "doc_ref": "proj-t330.server.0002.0001-D",
            },
            headers=auth_header("usr_t330_worker"),
        )

        assert resp.status_code == 200, f"HTTP {resp.status_code}: {resp.text}"
        body = resp.json()
        mention = body.get("mention")
        raw_token = body.get("raw_token")

        assert mention, "mention missing"
        assert raw_token, "raw_token missing"

        # Validation 1: check next_type
        next_type_line = None
        for line in mention.split("\n"):
            if line.startswith("next_type:"):
                next_type_line = line
                break

        assert next_type_line, "next_type line missing"
        actual_next_type = next_type_line.split("next_type:")[1].split("#")[0].strip()
        assert actual_next_type == "<Sequence undecided>", (
            f"expected '<Sequence undecided>', actual '{actual_next_type}'"
        )

        # Validation 2: Authorization token matches
        auth_line = None
        for line in mention.split("\n"):
            if "Authorization:" in line:
                auth_line = line
                break

        assert auth_line, "Authorization line missing"
        assert f"Bearer {raw_token}" in auth_line, (
            f"raw_token mismatch: raw_token={raw_token}, auth_line={auth_line}"
        )

    def test_case3_seq_head_in_progress(self, t330_client):
        """Case 3: Sequence in progress (head status=in_progress) ? next_type='<?? ?: D>'."""
        resp = t330_client.post(
            "/api/v1/token/issue",
            json={
                "project": "proj-t330",
                "group": "0003", "module": "server",
                "action_scope": "new",
                "doc_ref": "proj-t330.server.0003.0001-DS",
            },
            headers=auth_header("usr_t330_worker"),
        )

        assert resp.status_code == 200, f"HTTP {resp.status_code}: {resp.text}"
        body = resp.json()
        mention = body.get("mention")
        raw_token = body.get("raw_token")

        assert mention, "mention missing"
        assert raw_token, "raw_token missing"

        # Validation 1: check next_type
        next_type_line = None
        for line in mention.split("\n"):
            if line.startswith("next_type:"):
                next_type_line = line
                break

        assert next_type_line, "next_type line missing"
        actual_next_type = next_type_line.split("next_type:")[1].split("#")[0].strip()
        assert actual_next_type == "<In progress: D>", (
            f"expected '<In progress: D>', actual '{actual_next_type}'"
        )

        # Validation 2: Authorization token matches
        auth_line = None
        for line in mention.split("\n"):
            if "Authorization:" in line:
                auth_line = line
                break

        assert auth_line, "Authorization line missing"
        assert f"Bearer {raw_token}" in auth_line, (
            f"raw_token mismatch: raw_token={raw_token}, auth_line={auth_line}"
        )

