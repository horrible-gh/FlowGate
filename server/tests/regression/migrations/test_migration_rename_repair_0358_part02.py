"""Independently selectable scenarios; shared fixtures remain in test_migration_rename_repair_0358.py."""
from __future__ import annotations

from tests.regression.migrations.test_migration_rename_repair_0358 import (
    Path,
    _DEV_ORPHANS,
    _MIGRATIONS_DIR,
    _MOVED_FILE,
    _MOVED_HISTORIES,
    _RENAMED,
    _RENAMES,
    _SERVER_DIR,
    _disk_migrations,
    _fully_migrated_db,
    _letter_suffix_pairs,
    _migrated_db,
    _provider_columns,
    _rewind_moved_row,
    _token_columns,
    find_renamed_migrations,
    migration_key,
    migration_slug,
    os,
    pytest,
    reconcile_renamed_migrations,
    sqlite3,
)

def test_renamed_files_are_paired_with_the_name_they_ran_under():
    disk = _disk_migrations()
    applied = (set(disk) - set(_RENAMED.values())) | set(_RENAMED)
    pairs = find_renamed_migrations(applied, disk)
    assert pairs == sorted((old, new) for old, new in _RENAMED.items())
def test_the_rejected_boot_failure_reproduces_without_the_repair(tmp_path):
    from sqloader.migrator import DatabaseMigrator
    from sqloader.sqlite3 import SQLiteWrapper

    db_path = _fully_migrated_db(tmp_path)

    with pytest.raises(Exception) as excinfo:
        DatabaseMigrator(SQLiteWrapper(str(db_path)), str(_MIGRATIONS_DIR), auto_run=True)

    message = str(excinfo.value)
    assert _RENAMED["042_tokens_review_scope.sql"] in message, message
    assert "CHECK constraint failed" in message, message
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
