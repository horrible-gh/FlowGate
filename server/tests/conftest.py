"""Pytest configuration for FlowGate tests — shared fixtures and setup."""
import os
import sqlite3
import tempfile
from pathlib import Path

import pytest

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")

_SERVER_DIR = Path(__file__).resolve().parents[1]
_MIGRATIONS_DIR = _SERVER_DIR / "sql" / "migrations" / "sqlite"


def get_migration_files() -> list[Path]:
    """Get all migration files sorted in order."""
    files = sorted(_MIGRATIONS_DIR.glob("*.sql"))
    return files


@pytest.fixture(scope="session")
def all_migrations_db():
    """Create a test database with all migrations applied."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")

    # Apply all migrations in order
    for migration_file in get_migration_files():
        try:
            sql = migration_file.read_text(encoding="utf-8")
            conn.executescript(sql)
            print(f"✓ Applied: {migration_file.name}")
        except Exception as e:
            print(f"⚠ Error in {migration_file.name}: {e}")

    # Seed required data
    conn.executescript(
        """
        INSERT OR IGNORE INTO roles(role_id,role_name,is_system,created_at,updated_at)
            VALUES
                ('role_admin','Administrator',1,datetime('now'),datetime('now')),
                ('role_manager','Manager',1,datetime('now'),datetime('now')),
                ('role_worker','Worker',1,datetime('now'),datetime('now')),
                ('role_viewer','Viewer',1,datetime('now'),datetime('now'));

        INSERT OR IGNORE INTO permissions(permission_id,permission_name,created_at)
            VALUES
                ('system.settings.manage','Manage system settings',datetime('now')),
                ('system.user.read','View users',datetime('now')),
                ('system.user.create','Create user',datetime('now')),
                ('system.user.update','Update user',datetime('now')),
                ('system.user.delete','Delete user',datetime('now')),
                ('system.user.assign_role','Assign role',datetime('now')),
                ('project.settings.read','View project settings',datetime('now')),
                ('project.settings.edit','Edit project settings',datetime('now')),
                ('project.document_type.create','Create document type',datetime('now')),
                ('project.document_type.update','Update document type',datetime('now')),
                ('project.document_type.delete','Delete document type',datetime('now'));

        INSERT OR IGNORE INTO role_permissions(role_id,permission_id)
            VALUES
                ('role_admin','system.settings.manage'),
                ('role_admin','system.user.read'),
                ('role_admin','system.user.create'),
                ('role_admin','system.user.update'),
                ('role_admin','system.user.delete'),
                ('role_admin','system.user.assign_role'),
                ('role_admin','project.settings.read'),
                ('role_admin','project.settings.edit'),
                ('role_admin','project.document_type.create'),
                ('role_admin','project.document_type.update'),
                ('role_admin','project.document_type.delete'),
                ('role_manager','project.settings.read'),
                ('role_manager','project.settings.edit'),
                ('role_manager','project.document_type.create'),
                ('role_manager','project.document_type.update'),
                ('role_manager','project.document_type.delete');

        INSERT OR IGNORE INTO projects(project_id,project_name,is_active,created_at,updated_at)
            VALUES
                ('__SYSTEM__','[System]',1,datetime('now'),datetime('now'));

        INSERT OR IGNORE INTO users(user_id,username,email,password,is_active,is_admin,first_login_required,created_at,updated_at)
            VALUES
                ('usr_admin','admin','admin@test.com','hashed_pw',1,1,0,datetime('now'),datetime('now'));

        INSERT OR IGNORE INTO user_project_roles(user_id,project_id,role_id,granted_at)
            VALUES('usr_admin','__SYSTEM__','role_admin',datetime('now'));

        INSERT OR IGNORE INTO system_settings(setting_key,setting_value,value_type,updated_at)
            VALUES('storage_root','/data/flowgate','string',datetime('now'));
    """
    )

    conn.commit()
    yield conn
    conn.close()
    os.unlink(db_path)


@pytest.fixture
def test_db(all_migrations_db):
    """Provide a test database connection with all migrations applied."""
    yield all_migrations_db

