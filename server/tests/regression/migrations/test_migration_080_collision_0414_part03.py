"""Independently selectable scenarios; shared fixtures remain in test_migration_080_collision_0414.py."""
from __future__ import annotations

from tests.regression.migrations.test_migration_080_collision_0414 import (
    Path,
    RENAMES,
    _BARE_PROMPT_AUDIT,
    _BARE_PROVIDER,
    _MIGRATIONS_DIR,
    _NEW_PROMPT_AUDIT,
    _NEW_PROVIDER,
    _OLD_LETTERED_PROVIDER,
    _OLD_NUMBERED_PROVIDER,
    _PROVIDER_HISTORIES,
    _SERVER_DIR,
    _dev_preview_state_db,
    _disk_migrations,
    _migrate_fresh,
    _plant_provider_row,
    _provider_columns,
    _rewrite_ledger,
    apply_migration_renames,
    os,
    pytest,
    sqlite3,
)

def test_two_consecutive_boots_after_carryover_is_a_full_noop(tmp_path):
    db_path = _dev_preview_state_db(tmp_path, "reboot-twice.db")

    first = apply_migration_renames("sqlite3", sqlite_path=str(db_path))
    assert first >= 2

    from sqloader.migrator import DatabaseMigrator
    from sqloader.sqlite3 import SQLiteWrapper

    DatabaseMigrator(SQLiteWrapper(str(db_path)), str(_MIGRATIONS_DIR), auto_run=True)
    after_first_boot = {
        row[0]
        for row in sqlite3.connect(str(db_path)).execute("SELECT filename FROM migrations")
    }

    second = apply_migration_renames("sqlite3", sqlite_path=str(db_path))
    assert second == 0
    # A second real migrator construction must not attempt anything either.
    DatabaseMigrator(SQLiteWrapper(str(db_path)), str(_MIGRATIONS_DIR), auto_run=True)

    conn = sqlite3.connect(str(db_path))
    try:
        after_second_boot = {row[0] for row in conn.execute("SELECT filename FROM migrations")}
    finally:
        conn.close()
    assert after_second_boot == after_first_boot
def test_a_db_already_cleaned_up_to_080a_081a_is_untouched(tmp_path):
    db_path = tmp_path / "already_clean.db"
    _migrate_fresh(db_path)

    before = {
        row[0]
        for row in sqlite3.connect(str(db_path)).execute("SELECT filename FROM migrations")
    }
    assert {_NEW_PROMPT_AUDIT, _NEW_PROVIDER} <= before

    assert apply_migration_renames("sqlite3", sqlite_path=str(db_path)) == 0

    from sqloader.migrator import DatabaseMigrator
    from sqloader.sqlite3 import SQLiteWrapper

    DatabaseMigrator(SQLiteWrapper(str(db_path)), str(_MIGRATIONS_DIR), auto_run=True)
    conn = sqlite3.connect(str(db_path))
    try:
        after = {row[0] for row in conn.execute("SELECT filename FROM migrations")}
    finally:
        conn.close()
    assert after == before
