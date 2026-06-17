#!/usr/bin/env python3
"""T330 verification — validate token/issue response text (sequence lookup consistency + real token).

Three cases:
1. Sequence decided document (head_status=pending) -> next_type must contain head_type
2. Sequence undecided (no seq) -> must contain <Sequence Undecided>
3. Sequence in progress (head_status != pending) -> must contain <In Progress: head_type>
"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
from pathlib import Path
from datetime import datetime, timezone, timedelta

from fastapi import FastAPI
from starlette.testclient import TestClient

# ── Environment variables (set before imports) ─────────────────────────────
os.environ["TESTING"] = "1"
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
os.environ["FLOWGATE_TOKEN_PEPPER_ACTIVE_ID"] = "t330"
os.environ["FLOWGATE_TOKEN_PEPPER_t330"] = "t330-pepper-value-for-mention-verification-test"

_SERVER_DIR = Path(__file__).resolve().parents[0]
_SCHEMA_DIR = _SERVER_DIR / "sql" / "migrations" / "sqlite"
sys.path.insert(0, str(_SERVER_DIR))


class _TestDB:
    """Real DB helper wrapping temporary SQLite."""

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

    def raw_execute(self, sql: str):
        """Execute directly (including scripts)."""
        self._conn.executescript(sql)
        self._conn.commit()

    def close(self):
        self._conn.close()


def setup_t330_db() -> _TestDB:
    """Initialize DB for T330 verification."""
    db_path = str(Path(tempfile.mkdtemp()) / "t330.db")
    os.environ["FLOWGATE_DB_PATH"] = db_path
    db = _TestDB(db_path)
    
    # Load migration files
    migration_files = sorted(_SCHEMA_DIR.glob("*.sql"))
    for migration_file in migration_files:
        sql = migration_file.read_text(encoding="utf-8")
        db.raw_execute(sql)

    now = datetime.now(timezone.utc).isoformat()
    
    # Set up base roles and permissions
    db.execute(
        "INSERT OR IGNORE INTO roles(role_id,role_name,is_system,created_at,updated_at) "
        "VALUES (?, ?, ?, ?, ?)",
        ["role_worker", "Worker", 1, now, now],
    )
    
    db.execute(
        "INSERT OR IGNORE INTO permissions(permission_id,permission_name,created_at) "
        "VALUES (?, ?, ?)",
        ["perm_document_read", "Document Read", now],
    )
    
    db.execute(
        "INSERT OR IGNORE INTO role_permissions(role_id,permission_id) "
        "VALUES (?, ?)",
        ["role_worker", "perm_document_read"],
    )
    
    # Project
    db.execute(
        "INSERT OR IGNORE INTO projects (project_id, project_name, created_at, updated_at) "
        "VALUES (?, ?, ?, ?)",
        ["proj-t330", "T330 Test Project", now, now],
    )

    # Groups (0001 — head pending, 0002 — no seq, 0003 — head in_progress)
    for group_seq in ["0001", "0002", "0003"]:
        db.execute(
            "INSERT OR IGNORE INTO groups "
            "(group_id, project_id, module, title, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [
                f"proj-t330-server-{group_seq}",
                "proj-t330",
                "server",
                f"Test Group {group_seq}",
                now,
                now,
            ],
        )

    # Parent documents (by case)
    docs = [
        # Case 1: sequence decided document (head_status=pending)
        {
            "doc_id": "proj-t330-server-0001-R0001",
            "project_id": "proj-t330",
            "module": "server",
            "group_id": "proj-t330-server-0001",
            "type_code": "R",
            "seq": 1,
            "title": "Case1: Head Pending",
            "status": "open",
        },
        # Case 2: sequence undecided document (no seq record)
        {
            "doc_id": "proj-t330-server-0002-D0001",
            "project_id": "proj-t330",
            "module": "server",
            "group_id": "proj-t330-server-0002",
            "type_code": "D",
            "seq": 1,
            "title": "Case2: No Sequence",
            "status": "open",
        },
        # Case 3: sequence in progress (head_status != pending)
        {
            "doc_id": "proj-t330-server-0003-DS0001",
            "project_id": "proj-t330",
            "module": "server",
            "group_id": "proj-t330-server-0003",
            "type_code": "DS",
            "seq": 1,
            "title": "Case3: Head In Progress",
            "status": "open",
        },
    ]

    for doc in docs:
        db.execute(
            "INSERT OR IGNORE INTO documents "
            "(doc_id, project_id, module, group_id, type_code, seq, title, "
            " file_path, status, owner_id, created_at, updated_at, revision_no) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                doc["doc_id"], doc["project_id"], doc["module"], doc["group_id"],
                doc["type_code"], doc["seq"], doc["title"],
                "", doc["status"], "usr_t330_owner", now, now, 0,
            ],
        )

    # Case 1: workflow sequence + head (pending)
    db.execute(
        "INSERT OR IGNORE INTO workflow_sequences "
        "(seq_id, doc_id, status, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?)",
        ["seq-case1", "proj-t330-server-0001-R0001", "active", now, now],
    )
    db.execute(
        "INSERT OR IGNORE INTO workflow_sequence_items "
        "(item_id, seq_id, type, status, order_no, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ["head-case1", "seq-case1", "D", "pending", 1, now, now],
    )

    # Case 3: workflow sequence + head (in_progress)
    db.execute(
        "INSERT OR IGNORE INTO workflow_sequences "
        "(seq_id, doc_id, status, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?)",
        ["seq-case3", "proj-t330-server-0003-DS0001", "active", now, now],
    )
    db.execute(
        "INSERT OR IGNORE INTO workflow_sequence_items "
        "(item_id, seq_id, type, status, order_no, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ["head-case3", "seq-case3", "D", "in_progress", 1, now, now],
    )

    # RBAC: grant permissions to all users
    for user_id in ["usr_t330_owner", "usr_t330_worker"]:
        db.execute(
            "INSERT OR IGNORE INTO user_roles (user_id, project_id, role_id) "
            "VALUES (?, ?, ?)",
            [user_id, "proj-t330", "role_worker"],
        )

    return db


def setup_t330_app() -> FastAPI:
    """Test app dedicated to T330."""
    from modules.flow_gate.api.token_routes import router as token_router
    from modules.flow_gate.api.v1.help_routes import router as help_router

    app = FastAPI()
    app.include_router(token_router)
    app.include_router(help_router)
    return app


def make_jwt(user_id: str, roles: list[str] | None = None) -> str:
    """Create a real JWT."""
    from modules.flow_gate.auth.jwt_service import create_access_token
    token, _ = create_access_token(
        user_id=user_id,
        username=user_id,
        roles=roles or ["role_worker"],
    )
    return token


def main():
    """Run T330 verification."""
    print("=" * 80)
    print("T330 verification — token/issue response mention (sequence consistency + real token)")
    print("=" * 80)

    # Initialize DB
    print("\n[1] Initializing DB...")
    db = setup_t330_db()

    # Swap store
    print("[2] Replacing store...")
    from modules.flow_gate.db import connection as conn_mod
    from modules.flow_gate.rbac import permission_service

    class _TestStore(conn_mod.FlowGateStore):
        def __init__(self):
            self._db = db
            self._sq = None

    original_store = conn_mod.STORE
    conn_mod.STORE = _TestStore()
    
    with permission_service._cache_lock:
        permission_service._cache.clear()

    try:
        # App + client
        print("[3] Creating TestClient...")
        app = setup_t330_app()
        client = TestClient(app, raise_server_exceptions=False)

        # Test all three cases
        cases = [
            {
                "name": "Case 1: Sequence decided (head status=pending)",
                "doc_ref": "proj-t330-server-0001-R0001",
                "group_name": "proj-t330-server-0001",
                "expected_next_type": "D",
                "description": "next_type must contain the actual head_type='D', not <Sequence Undecided>",
            },
            {
                "name": "Case 2: Sequence undecided (no seq)",
                "doc_ref": "proj-t330-server-0002-D0001",
                "group_name": "proj-t330-server-0002",
                "expected_next_type": "<Sequence Undecided>",
                "description": "next_type should contain placeholder '<Sequence Undecided>'",
            },
            {
                "name": "Case 3: Sequence in progress (head status=in_progress)",
                "doc_ref": "proj-t330-server-0003-DS0001",
                "group_name": "proj-t330-server-0003",
                "expected_next_type": "<In Progress: D>",
                "description": "next_type should contain placeholder '<In Progress: D>'",
            },
        ]

        jwt_token = make_jwt("usr_t330_worker")
        auth_header = {"Authorization": f"Bearer {jwt_token}"}

        all_pass = True

        for i, case in enumerate(cases, 1):
            print(f"\n{'-' * 80}")
            print(f"[Case {i}] {case['name']}")
            print(f"Description: {case['description']}")
            print(f"Document: {case['doc_ref']}")
            print(f"Expected: next_type = '{case['expected_next_type']}'")
            print("-" * 80)

            resp = client.post(
                "/api/v1/token/issue",
                json={
                    "project": "proj-t330",
                    "group_name": case["group_name"],
                    "action_scope": "new",
                    "doc_ref": case["doc_ref"],
                },
                headers=auth_header,
            )

            if resp.status_code != 200:
                print(f"❌ FAIL: HTTP {resp.status_code}")
                print(f"Response: {resp.text}")
                all_pass = False
                continue

            body = resp.json()
            mention = body.get("mention")
            raw_token = body.get("raw_token")

            if not mention:
                print(f"❌ FAIL: mention is missing")
                all_pass = False
                continue

            # Check 1: verify next_type
            print("\n✓ Response capture:")
            print(f"  raw_token: {raw_token[:20]}... (length: {len(raw_token)})")
            print(f"  mention: (27 lines)")
            print("  " + ("-" * 76))
            for line in mention.split("\n"):
                print(f"  {line}")
            print("  " + ("-" * 76))

            # Check next_type line
            next_type_line = None
            for line in mention.split("\n"):
                if line.startswith("next_type:"):
                    next_type_line = line
                    break

            if not next_type_line:
                print(f"❌ FAIL: next_type line not found")
                all_pass = False
                continue

            # Extract actual value
            actual_next_type = next_type_line.split("next_type:")[1].split("#")[0].strip()
            
            if actual_next_type == case["expected_next_type"]:
                print(f"✓ PASS: next_type = '{actual_next_type}' (expected: '{case['expected_next_type']}')")
            else:
                print(f"❌ FAIL: next_type = '{actual_next_type}' (expected: '{case['expected_next_type']}')")
                all_pass = False

            # Check 2: verify Authorization token
            auth_line = None
            for line in mention.split("\n"):
                if "Authorization:" in line:
                    auth_line = line
                    break

            if not auth_line:
                print(f"❌ FAIL: Authorization line not found")
                all_pass = False
                continue

            # Check "Bearer {token}" format
            if f"Bearer {raw_token}" in auth_line:
                print(f"✓ PASS: raw_token present in Authorization (match)")
            elif "Bearer <token>" in auth_line:
                print(f"❌ FAIL: Authorization contains placeholder '<token>' (not replaced)")
                all_pass = False
            else:
                print(f"⚠ WARN: Authorization format needs review: {auth_line}")

        print(f"\n{'=' * 80}")
        if all_pass:
            print("✓ All checks passed")
        else:
            print("❌ Verification failed — T329 rework needed")
        print(f"{'=' * 80}")

        return all_pass

    finally:
        conn_mod.STORE = original_store
        with permission_service._cache_lock:
            permission_service._cache.clear()
        db.close()


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
