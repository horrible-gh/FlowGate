"""flowgate.default.0358 R0001 4번째 반려 — a renamed migration must not run twice.

The rejected boot log is one line:

    Database Migration Failed.Failed to apply migration 042_tokens_review_scope.sql:
    CHECK constraint failed: action_scope IN ('new', 'edit', 'workflow_decide', 'review')

sqloader's DatabaseMigrator recognises an applied migration only by its exact
filename, so a database that recorded `042a_tokens_review_scope.sql` treats today's
`042_tokens_review_scope.sql` as pending and executes it again — against a tokens
table that six later migrations have widened.

These tests reproduce that boot failure on a real sqlite database through the real
migrator (not a stand-in), then prove the repair in server/migration_rename_repair.py
lets the same boot finish without losing a column or a row, and that it never hides a
migration that genuinely has not run.
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest

# `import config` instantiates Settings() at import time, so the required keys must
# be in the environment before then — same preamble as
# tests/test_pool_config_and_auth_load_0288.py, which imports config for the same
# reason. TESTING=1 keeps that import from building a real DatabaseSetting.
os.environ["TESTING"] = "1"
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
os.environ.setdefault("ALLOWED_ORIGIN", "*")
os.environ.setdefault("CONTEXT", "/flowgate")
os.environ.setdefault("DB_TYPE", "sqlite3")

from migration_rename_repair import (
    find_renamed_migrations,
    migration_key,
    reconcile_renamed_migrations,
)

_SERVER_DIR = Path(__file__).resolve().parents[1]
_MIGRATIONS_DIR = _SERVER_DIR / "sql" / "migrations" / "sqlite"

# What the dev deployment's migrations table holds for the six files whose number
# collided with a parallel branch and were later disambiguated. Left column: the
# name that database recorded. Right column: the name shipped today.
_RENAMED = {
    "031a_remove_unused_log_doctype.sql": "031_remove_unused_log_doctype.sql",
    "042a_tokens_review_scope.sql": "042_tokens_review_scope.sql",
    "043a_tokens_dry_run_count.sql": "043_tokens_dry_run_count.sql",
    "062a_tokens_workflow_sequence_edit_scope.sql": (
        "062_tokens_workflow_sequence_edit_scope.sql"
    ),
    "063a_tokens_conflict_merge_id.sql": "063_tokens_conflict_merge_id.sql",
    "067a_auth_sessions.sql": "067_auth_sessions.sql",
}


def _disk_migrations() -> list[str]:
    return sorted(p.name for p in _MIGRATIONS_DIR.glob("*.sql"))


def _fully_migrated_db(tmp_path: Path) -> Path:
    """A database migrated the way the server migrates it, then aged.

    Rewrites the six bookkeeping rows to the older filenames and leaves a token row
    whose action_scope only exists because later migrations widened the CHECK — the
    exact combination that made the deployment's boot fail.
    """
    from sqloader.migrator import DatabaseMigrator
    from sqloader.sqlite3 import SQLiteWrapper

    db_path = tmp_path / "deployment.db"
    DatabaseMigrator(SQLiteWrapper(str(db_path)), str(_MIGRATIONS_DIR), auto_run=True)

    conn = sqlite3.connect(str(db_path))
    try:
        for old_name, current_name in _RENAMED.items():
            conn.execute(
                "UPDATE migrations SET filename = ? WHERE filename = ?",
                (old_name, current_name),
            )
        conn.execute(
            "INSERT INTO tokens (token_id, hash, pepper_id, project, doc_ref, "
            "action_scope, issued_to, created_at, expires_at) VALUES "
            "('tok_live', 'h', 'p1', 'flowgate', 'flowgate.default.0358.0005-TR', "
            "'resolve_conflict', 'u1', '2026-08-16T00:00:00', '2026-08-17T00:00:00')"
        )
        conn.commit()
    finally:
        conn.close()
    return db_path


def _token_columns(db_path: Path) -> set[str]:
    conn = sqlite3.connect(str(db_path))
    try:
        return {row[1] for row in conn.execute("PRAGMA table_info(tokens)")}
    finally:
        conn.close()


# ── the key: same number + same slug, nothing looser ────────────────────────


def test_letter_suffix_is_the_only_difference_a_key_ignores():
    assert migration_key("042a_tokens_review_scope.sql") == migration_key(
        "042_tokens_review_scope.sql"
    )
    # Same number, different migration — these must never be treated as one file.
    assert migration_key("042_project_messages.sql") != migration_key(
        "042_tokens_review_scope.sql"
    )
    # A number that is not a rename of anything keeps its own identity.
    assert migration_key("074_test_run_cancel_status.sql") != migration_key(
        "074a_ai_invoke_chain_progress.sql"
    )


def test_every_shipped_migration_has_a_unique_key():
    """A duplicate key would let one file mark an unrelated one as applied."""
    names = _disk_migrations()
    keys = [migration_key(name) for name in names]
    assert len(set(keys)) == len(names)


def test_pending_migrations_without_an_applied_sibling_are_left_alone():
    """Genuinely new files must stay pending — the repair may not mask them."""
    disk = _disk_migrations()
    applied = set(disk) - {
        "074_test_run_cancel_status.sql",
        "075_test_run_cases_fk_repair.sql",
    }
    assert find_renamed_migrations(applied, disk) == []


def test_renamed_files_are_paired_with_the_name_they_ran_under():
    disk = _disk_migrations()
    applied = (set(disk) - set(_RENAMED.values())) | set(_RENAMED)
    pairs = find_renamed_migrations(applied, disk)
    assert pairs == sorted((old, new) for old, new in _RENAMED.items())


# ── the rejected boot, reproduced and then fixed ─────────────────────────────


def test_the_rejected_boot_failure_reproduces_without_the_repair(tmp_path):
    from sqloader.migrator import DatabaseMigrator
    from sqloader.sqlite3 import SQLiteWrapper

    db_path = _fully_migrated_db(tmp_path)

    with pytest.raises(Exception) as excinfo:
        DatabaseMigrator(SQLiteWrapper(str(db_path)), str(_MIGRATIONS_DIR), auto_run=True)

    message = str(excinfo.value)
    assert "042_tokens_review_scope.sql" in message
    assert "CHECK constraint failed" in message


def test_repair_lets_the_same_boot_finish_without_losing_a_column_or_row(tmp_path):
    from sqloader.migrator import DatabaseMigrator
    from sqloader.sqlite3 import SQLiteWrapper

    db_path = _fully_migrated_db(tmp_path)
    before = _token_columns(db_path)

    db = SQLiteWrapper(str(db_path))
    migrator = DatabaseMigrator(db, str(_MIGRATIONS_DIR), auto_run=False)
    repaired = reconcile_renamed_migrations(db, str(_MIGRATIONS_DIR))
    migrator.apply_migrations()

    assert sorted(new for _old, new in repaired) == sorted(_RENAMED.values())

    conn = sqlite3.connect(str(db_path))
    try:
        applied = {row[0] for row in conn.execute("SELECT filename FROM migrations")}
        # Every file on disk is now accounted for, so the next boot applies nothing.
        assert set(_disk_migrations()) <= applied
        # 042/062 rebuild tokens with their own era's columns; a second run would
        # have dropped everything added after them.
        assert _token_columns(db_path) == before
        assert {"dry_run_count", "merge_id", "continuation_locale"} <= before
        row = conn.execute(
            "SELECT action_scope FROM tokens WHERE token_id = 'tok_live'"
        ).fetchone()
        assert row is not None and row[0] == "resolve_conflict"
    finally:
        conn.close()


def test_repair_writes_nothing_when_the_names_already_line_up(tmp_path):
    from sqloader.migrator import DatabaseMigrator
    from sqloader.sqlite3 import SQLiteWrapper

    db_path = tmp_path / "healthy.db"
    db = SQLiteWrapper(str(db_path))
    DatabaseMigrator(db, str(_MIGRATIONS_DIR), auto_run=True)

    assert reconcile_renamed_migrations(db, str(_MIGRATIONS_DIR)) == []

    conn = sqlite3.connect(str(db_path))
    try:
        applied = [row[0] for row in conn.execute("SELECT filename FROM migrations")]
        assert sorted(applied) == _disk_migrations()
    finally:
        conn.close()


def test_repair_is_idempotent_across_boots(tmp_path):
    from sqloader.sqlite3 import SQLiteWrapper

    db_path = _fully_migrated_db(tmp_path)
    db = SQLiteWrapper(str(db_path))
    assert len(reconcile_renamed_migrations(db, str(_MIGRATIONS_DIR))) == len(_RENAMED)
    assert reconcile_renamed_migrations(db, str(_MIGRATIONS_DIR)) == []


# ── the boot path that actually runs it ──────────────────────────────────────


def test_config_run_migrations_repairs_before_it_applies(tmp_path):
    """DatabaseSetting.run_migrations is what replaced sqloader's migration block."""
    import config
    from sqloader.sqlite3 import SQLiteWrapper

    db_path = _fully_migrated_db(tmp_path)
    setting = object.__new__(config.DatabaseSetting)
    setting.db_instance = SQLiteWrapper(str(db_path))
    setting.migrator = None

    setting.run_migrations(
        {"auto_migration": True, "migration_path": str(_MIGRATIONS_DIR)}
    )

    assert setting.migrator is not None
    conn = sqlite3.connect(str(db_path))
    try:
        applied = {row[0] for row in conn.execute("SELECT filename FROM migrations")}
        assert set(_disk_migrations()) <= applied
    finally:
        conn.close()


def test_config_run_migrations_still_fails_loudly_on_a_real_migration_error(tmp_path):
    """The repair must not turn a genuine migration failure into a silent boot."""
    import config
    from sqloader.sqlite3 import SQLiteWrapper

    broken_dir = tmp_path / "migrations"
    broken_dir.mkdir()
    (broken_dir / "001_broken.sql").write_text("SELECT * FROM no_such_table;", "utf-8")

    setting = object.__new__(config.DatabaseSetting)
    setting.db_instance = SQLiteWrapper(str(tmp_path / "broken.db"))
    setting.migrator = None

    with pytest.raises(SystemExit) as excinfo:
        setting.run_migrations(
            {"auto_migration": True, "migration_path": str(broken_dir)}
        )
    assert excinfo.value.code == 1
