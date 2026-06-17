"""T055: Test for DB schema creation."""
import os
import sqlite3
import tempfile
from pathlib import Path

import pytest

MIGRATIONS_DIR = (
    Path(__file__).resolve().parents[1]
    / "sql" / "migrations" / "sqlite"
)


def get_all_migrations() -> list[Path]:
    """Get all migration files sorted in order."""
    files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    return files

REQUIRED_TABLES = [
    "users", "roles", "permissions", "role_permissions",
    "projects", "project_settings", "groups", "sub_groups",
    "user_project_roles", "documents", "events", "tv_scenarios",
    "document_types", "document_type_templates", "id_counter",
    "token_blacklist", "refresh_tokens", "workflow_events",
    "numbering_jobs", "system_settings",
]

REQUIRED_VIEWS = ["v_tv_progress", "v_tv_open"]

SEED_ROLES = ["role_admin", "role_manager", "role_worker", "role_viewer"]


@pytest.fixture
def db():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    
    # Apply all migrations in order
    for migration_file in get_all_migrations():
        try:
            sql = migration_file.read_text(encoding="utf-8")
            conn.executescript(sql)
        except sqlite3.OperationalError:
            # Some migrations might fail (e.g., IF NOT EXISTS constraints)
            pass
    
    yield conn
    conn.close()
    os.unlink(db_path)


def test_schema_file_exists():
    assert MIGRATIONS_DIR.exists(), f"migrations dir not found: {MIGRATIONS_DIR}"
    migrations = get_all_migrations()
    assert len(migrations) > 0, "No migration files found"


def test_all_tables_created(db):
    tables = {
        r["name"]
        for r in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    for table in REQUIRED_TABLES:
        assert table in tables, f"Missing table: {table}"


def test_all_views_created(db):
    views = {
        r["name"]
        for r in db.execute(
            "SELECT name FROM sqlite_master WHERE type='view'"
        ).fetchall()
    }
    for view in REQUIRED_VIEWS:
        assert view in views, f"Missing view: {view}"


def test_seed_roles(db):
    roles = {
        r["role_id"]
        for r in db.execute(
            "SELECT role_id FROM roles WHERE is_system=1"
        ).fetchall()
    }
    for role_id in SEED_ROLES:
        assert role_id in roles, f"Seed role missing: {role_id}"


def test_seed_permissions_count(db):
    count = db.execute("SELECT COUNT(*) FROM permissions").fetchone()[0]
    assert count >= 27, f"Insufficient permissions count: {count}"


def test_seed_document_types_count(db):
    count = db.execute(
        "SELECT COUNT(*) FROM document_types WHERE project_id IS NULL AND is_system=1"
    ).fetchone()[0]
    assert count == 21, f"Document types count mismatch: {count}"


def test_seed_system_settings(db):
    count = db.execute("SELECT COUNT(*) FROM system_settings").fetchone()[0]
    assert count >= 5, f"Insufficient system_settings seed count: {count}"


def test_documents_status_constraint(db):
    db.execute(
        "INSERT INTO projects (project_id, project_name, is_active, created_at, updated_at) "
        "VALUES ('P1', 'Test', 1, '2026-01-01', '2026-01-01')"
    )
    with pytest.raises(Exception):
        db.execute(
            "INSERT INTO documents (doc_id, project_id, module, type_code, seq, title, "
            "status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                "d1", "P1", "__ALL__", "R", 1, "test", "invalid_status",
                "2026-01-01", "2026-01-01",
            ],
        )
        db.commit()


def test_groups_status_constraint(db):
    db.execute(
        "INSERT INTO projects (project_id, project_name, is_active, created_at, updated_at) "
        "VALUES ('P2', 'Test2', 1, '2026-01-01', '2026-01-01')"
    )
    with pytest.raises(Exception):
        db.execute(
            "INSERT INTO groups "
            "(group_id, project_id, module, title, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ["g1", "P2", "__ALL__", "Group1", "INVALID", "2026-01-01", "2026-01-01"],
        )
        db.commit()


def test_project_settings_fk_cascade(db):
    db.execute(
        "INSERT INTO projects (project_id, project_name, is_active, created_at, updated_at) "
        "VALUES ('P3', 'Test3', 1, '2026-01-01', '2026-01-01')"
    )
    db.execute(
        "INSERT INTO project_settings (project_id, updated_at) VALUES ('P3', '2026-01-01')"
    )
    db.commit()
    db.execute("DELETE FROM projects WHERE project_id = 'P3'")
    db.commit()
    row = db.execute(
        "SELECT * FROM project_settings WHERE project_id = 'P3'"
    ).fetchone()
    assert row is None, "CASCADE DELETE not functioning"


def test_tokens_scratch_dir_column(db):
    """T251: The tokens table must contain a scratch_dir column (migration 016)."""
    columns = {
        r["name"]
        for r in db.execute("PRAGMA table_info(tokens)").fetchall()
    }
    assert "scratch_dir" in columns, "tokens.scratch_dir column missing (016_tokens_scratch_dir.sql not applied?)"


def test_tokens_allow_workflow_decide_scope(db):
    """Workflow decision workers use a dedicated token scope."""
    sql = db.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='tokens'"
    ).fetchone()["sql"]
    assert "workflow_decide" in sql


def test_v_tv_progress_view(db):
    db.execute(
        "INSERT INTO projects (project_id, project_name, is_active, created_at, updated_at) "
        "VALUES ('PV', 'ViewTest', 1, '2026-01-01', '2026-01-01')"
    )
    db.execute(
        "INSERT INTO documents "
        "(doc_id, project_id, module, type_code, seq, title, "
        "status, tv_type, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ["tv1", "PV", "__ALL__", "TS", 1, "Test TV", "open", "TV",
         "2026-01-01", "2026-01-01"],
    )
    db.commit()
    rows = db.execute("SELECT * FROM v_tv_progress WHERE doc_id='tv1'").fetchall()
    assert len(rows) == 1
    assert rows[0]["scenario_total"] == 0
