"""0272 — the installer must wait for migrations, not for the DB file.

Regression coverage for `sqlite3.IntegrityError: FOREIGN KEY constraint failed`
during install: setup.ps1 treated "flowgate.db exists" as "DB is ready", but
SQLite creates that file when sqloader connects, i.e. before 004_rbac.sql seeds
the __SYSTEM__ project and role rows create_dev_user.py inserts against.
"""
import importlib.util
import sqlite3
import sys
from pathlib import Path

import pytest

_SERVER_DIR = Path(__file__).resolve().parents[1]
_MIGRATIONS_DIR = _SERVER_DIR / "sql" / "migrations" / "sqlite"


def _load(module_name: str):
    """Import a top-level server script by path (they are not a package)."""
    if str(_SERVER_DIR) not in sys.path:
        sys.path.insert(0, str(_SERVER_DIR))
    spec = importlib.util.spec_from_file_location(module_name, _SERVER_DIR / f"{module_name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


check_db_ready = _load("check_db_ready")


def _migration_names() -> list[str]:
    return sorted(f.name for f in _MIGRATIONS_DIR.glob("*.sql"))


def _make_db(path: Path, applied: list[str]) -> None:
    """Build a DB whose sqloader tracking table lists exactly `applied`."""
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS migrations ("
        " filename TEXT PRIMARY KEY, applied_at DATETIME DEFAULT CURRENT_TIMESTAMP)"
    )
    conn.executemany("INSERT INTO migrations (filename) VALUES (?)", [(m,) for m in applied])
    conn.commit()
    conn.close()


def test_reports_ready_only_when_every_migration_applied(tmp_path):
    db = tmp_path / "flowgate.db"
    _make_db(db, _migration_names())
    assert check_db_ready.pending(str(db), str(_MIGRATIONS_DIR)) == set()


def test_partially_migrated_db_is_not_ready(tmp_path):
    """The exact failure state: file exists, RBAC seed has not run yet."""
    names = _migration_names()
    db = tmp_path / "flowgate.db"
    _make_db(db, names[:2])  # 001, 002 committed — 004_rbac.sql has not

    left = check_db_ready.pending(str(db), str(_MIGRATIONS_DIR))
    assert left, "a half-migrated DB must not be reported ready"
    assert any(n.startswith("004_") for n in left)


def test_freshly_created_empty_db_is_not_ready(tmp_path):
    """sqlite3.connect() alone creates the file; that must not count as ready."""
    db = tmp_path / "flowgate.db"
    sqlite3.connect(db).close()
    assert db.exists()
    # `migrations` table absent -> unreadable -> not ready
    assert check_db_ready.pending(str(db), str(_MIGRATIONS_DIR)) is None


def test_missing_db_is_not_ready_and_is_not_created(tmp_path):
    db = tmp_path / "absent.db"
    assert check_db_ready.pending(str(db), str(_MIGRATIONS_DIR)) is None
    assert not db.exists(), "the readiness probe must not create the DB it checks"


def test_cli_exit_codes(tmp_path):
    db = tmp_path / "flowgate.db"
    _make_db(db, _migration_names()[:2])
    argv = ["check_db_ready.py", "--db", str(db), "--migrations", str(_MIGRATIONS_DIR), "--quiet"]

    sys.argv = argv
    assert check_db_ready.main() == 1

    _make_db(tmp_path / "full.db", _migration_names())
    sys.argv = ["check_db_ready.py", "--db", str(tmp_path / "full.db"),
                "--migrations", str(_MIGRATIONS_DIR), "--quiet"]
    assert check_db_ready.main() == 0


def test_create_dev_user_reports_missing_rbac_seed(all_migrations_db):
    """With migrations applied the guard is silent; without the seed it names the gap."""
    create_dev_user = _load("create_dev_user")

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    # Schema present (001) but RBAC seed (004) missing — the reported bug's state.
    conn.execute("CREATE TABLE projects (project_id TEXT PRIMARY KEY)")
    conn.execute("CREATE TABLE roles (role_id TEXT PRIMARY KEY)")
    missing = create_dev_user.missing_rbac_seed(conn)
    assert "projects.__SYSTEM__" in missing
    assert "roles.role_admin" in missing

    conn.execute("INSERT INTO projects VALUES ('__SYSTEM__')")
    conn.executemany("INSERT INTO roles VALUES (?)", [("role_admin",), ("role_worker",)])
    assert create_dev_user.missing_rbac_seed(conn) == []


def test_create_dev_user_guard_passes_on_fully_migrated_db(all_migrations_db):
    """The seeded rows really do come from the migration set (not just our stub)."""
    create_dev_user = _load("create_dev_user")
    conn = sqlite3.connect(all_migrations_db) if isinstance(all_migrations_db, str) else all_migrations_db
    conn.row_factory = sqlite3.Row
    assert create_dev_user.missing_rbac_seed(conn) == []
