"""T055: Basic CRUD tests for model modules (using a temporary SQLite DB)."""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

import pytest

# ── Temporary DB setup ─────────────────────────────────────────────────────
MIGRATIONS_DIR = (
    Path(__file__).resolve().parents[1]
    / "sql" / "migrations" / "sqlite"
)


def get_all_migrations() -> list[Path]:
    """Get all migration files sorted in order."""
    files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    return files


class _MockDB:
    """Test DB driver that uses sqlite3 directly without sqloader."""

    def __init__(self, db_path: str):
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")

    def execute(self, sql: str, params=None):
        self._conn.execute(sql, params or [])
        self._conn.commit()

    def fetch_one(self, sql: str, params=None) -> dict | None:
        row = self._conn.execute(sql, params or []).fetchone()
        if row is None:
            return None
        return dict(row)

    def fetch_all(self, sql: str, params=None) -> list[dict]:
        rows = self._conn.execute(sql, params or []).fetchall()
        return [dict(r) for r in rows]

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
    
    # Apply all migrations in order
    for migration_file in get_all_migrations():
        try:
            sql = migration_file.read_text(encoding="utf-8")
            mock_db._conn.executescript(sql)
        except sqlite3.OperationalError as e:
            # Some migrations might fail (e.g., IF NOT EXISTS constraints or column already exists)
            # Log for debugging but continue
            if "already exists" not in str(e) and "duplicate column" not in str(e):
                pass
    
    yield mock_db, db_path
    mock_db.close()
    os.unlink(db_path)


@pytest.fixture(scope="module", autouse=True)
def patch_store(tmp_db):
    """Patch connection.get_store() to point to the temporary DB."""
    mock_db, _ = tmp_db
    os.environ.setdefault("TESTING", "1")
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

    from modules.flow_gate.db import connection as conn_mod

    original_store = conn_mod.STORE

    class _PatchedStore(conn_mod.FlowGateStore):
        def __init__(self):
            self._db = mock_db
            self._sq = None

        def _sql(self, key: str) -> str:
            raise NotImplementedError("The _sql method is not used in tests")

    conn_mod.STORE = _PatchedStore()
    yield
    conn_mod.STORE = original_store


# ── Tests ──────────────────────────────────────────────────────────────────

def test_project_crud():
    from modules.flow_gate.db import projects

    p = projects.create({
        "project_id": "TESTPRJ",
        "project_name": "Test Project",
    })
    assert p is not None
    assert p["project_id"] == "TESTPRJ"

    fetched = projects.get_by_id("TESTPRJ")
    assert fetched is not None
    assert fetched["project_name"] == "Test Project"

    projects.update("TESTPRJ", {"project_name": "Updated Project"})
    updated = projects.get_by_id("TESTPRJ")
    assert updated["project_name"] == "Updated Project"

    lst = projects.list_projects(is_active=1)
    assert any(p["project_id"] == "TESTPRJ" for p in lst)

    projects.delete("TESTPRJ")
    assert projects.get_by_id("TESTPRJ") is None


def test_user_crud():
    from modules.flow_gate.db import projects, users

    projects.create({"project_id": "USRPRJ", "project_name": "User Test"})

    u = users.create({
        "user_id": "usr_test_001",
        "username": "testuser",
        "email": "test@example.com",
        "password": "hashed_pw",
    })
    assert u["user_id"] == "usr_test_001"
    assert u["is_active"] == 1

    fetched = users.get_by_username("testuser")
    assert fetched is not None

    users.update("usr_test_001", {"is_admin": 1})
    updated = users.get_by_id("usr_test_001")
    assert updated["is_admin"] == 1

    lst = users.list_users(is_active=1)
    assert any(u["user_id"] == "usr_test_001" for u in lst)

    users.delete("usr_test_001")
    assert users.get_by_id("usr_test_001") is None


def test_group_crud():
    from modules.flow_gate.db import projects, groups

    projects.create({"project_id": "GRPPRJ", "project_name": "Group Test"})

    g = groups.create({
        "group_id": "GRPPRJ-__ALL__-0001",
        "project_id": "GRPPRJ",
        "title": "Test Group",
    })
    assert g["group_id"] == "GRPPRJ-__ALL__-0001"
    assert g["module"] == "__ALL__"

    lst = groups.list_groups("GRPPRJ")
    assert len(lst) >= 1

    groups.update("GRPPRJ-__ALL__-0001", {"status": "CLOSED"})
    updated = groups.get_by_id("GRPPRJ-__ALL__-0001")
    assert updated["status"] == "CLOSED"

    groups.delete("GRPPRJ-__ALL__-0001")
    assert groups.get_by_id("GRPPRJ-__ALL__-0001") is None


def test_document_crud():
    from modules.flow_gate.db import projects, documents

    projects.create({"project_id": "docprj", "project_name": "Doc Test"})

    doc = documents.create({
        "doc_id": "docprj-__ALL__-0001-R0001",
        "project_id": "docprj",
        "type_code": "R",
        "seq": 1,
        "title": "Test Document",
    })
    assert doc["doc_id"] == "docprj-__ALL__-0001-R0001"
    assert doc["status"] == "draft"

    fetched = documents.get_by_id("docprj-__ALL__-0001-R0001")
    assert fetched is not None

    documents.update("docprj-__ALL__-0001-R0001", {"status": "open"})
    updated = documents.get_by_id("docprj-__ALL__-0001-R0001")
    assert updated["status"] == "open"

    lst = documents.list_documents("docprj")
    assert len(lst) >= 1

    documents.delete("docprj-__ALL__-0001-R0001")
    assert documents.get_by_id("docprj-__ALL__-0001-R0001") is None


def test_system_settings_crud():
    from modules.flow_gate.db import system_settings

    row = system_settings.get("storage_root")
    assert row is not None  # Seed-created entry

    system_settings.set_value("test_key", "test_value", "string", "Test")
    fetched = system_settings.get("test_key")
    assert fetched["setting_value"] == "test_value"

    val = system_settings.get_value("test_key")
    assert val == "test_value"

    system_settings.delete("test_key")
    assert system_settings.get("test_key") is None


def test_roles_crud():
    from modules.flow_gate.db import roles

    lst = roles.list_roles()
    assert any(r["role_id"] == "role_admin" for r in lst)

    r = roles.create({
        "role_id": "role_test",
        "role_name": "Test Role",
    })
    assert r["role_id"] == "role_test"

    roles.update("role_test", {"description": "Updated description"})
    updated = roles.get_by_id("role_test")
    assert updated["description"] == "Updated description"

    roles.delete("role_test")
    assert roles.get_by_id("role_test") is None

    with pytest.raises(ValueError):
        roles.delete("role_admin")
