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

@pytest.mark.parametrize(
    "applied, disk",
    [
        # one row gone, two pending files claim the same slug
        ({"090_thing.sql"}, ["091_thing.sql", "092_thing.sql"]),
        # two rows gone, one pending file — which one did it run as?
        ({"088_thing.sql", "090_thing.sql"}, ["091_thing.sql"]),
    ],
)
def test_an_ambiguous_slug_is_left_pending(applied, disk):
    """Ambiguity fails loudly (the migrator runs the file) rather than silently."""
    assert find_renamed_migrations(applied, disk) == []
@pytest.mark.parametrize("history", _MOVED_HISTORIES)
def test_the_rejected_boot_reproduces_for_every_history_without_the_repair(tmp_path, history):
    """대조군 — 반려 사유가 세 이력 모두에서 글자 그대로 재현된다."""
    from sqloader.migrator import DatabaseMigrator
    from sqloader.sqlite3 import SQLiteWrapper

    db_path = _migrated_db(tmp_path, f"moved_{history[:4]}.db")
    _rewind_moved_row(db_path, history)

    with pytest.raises(Exception) as excinfo:
        DatabaseMigrator(SQLiteWrapper(str(db_path)), str(_MIGRATIONS_DIR), auto_run=True)

    message = str(excinfo.value)
    assert _MOVED_FILE in message, message
    assert "duplicate column name: provider_id" in message, message
@pytest.mark.parametrize("history", _MOVED_HISTORIES)
def test_the_repair_lets_that_boot_finish_for_every_history(tmp_path, history):
    from sqloader.migrator import DatabaseMigrator
    from sqloader.sqlite3 import SQLiteWrapper

    db_path = _migrated_db(tmp_path, f"repaired_{history[:4]}.db")
    _rewind_moved_row(db_path, history)
    before = _provider_columns(db_path)
    assert before == ["provider_id", "provider_display_name"]

    db = SQLiteWrapper(str(db_path))
    migrator = DatabaseMigrator(db, str(_MIGRATIONS_DIR), auto_run=False)
    repaired = reconcile_renamed_migrations(db, str(_MIGRATIONS_DIR))
    migrator.apply_migrations()

    assert repaired == [(history, _MOVED_FILE)]
    assert _provider_columns(db_path) == before

    conn = sqlite3.connect(str(db_path))
    try:
        applied = {row[0] for row in conn.execute("SELECT filename FROM migrations")}
    finally:
        conn.close()
    assert set(_disk_migrations()) <= applied
def test_config_run_migrations_survives_the_dev_deployment_ledger(tmp_path):
    """The real boot path, against the real ledger shape.

    `run_migrations` is what prints "Starting Database Migrator" and
    "Database Migration Failed.…" — the two lines of the rejection. Feed it a
    database whose ledger carries the dev deployment's three dead rows and whose
    089 has genuinely never run, and it has to come out the other side with the
    moved file recorded, the new table created, and the provider columns intact.
    """
    import config
    from sqloader.sqlite3 import SQLiteWrapper

    db_path = _migrated_db(tmp_path, "dev_shaped.db")
    _rewind_moved_row(db_path, "080b_workflow_sequence_provider.sql")
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("DELETE FROM migrations WHERE filename = '089_user_ui_settings.sql'")
        conn.execute("DROP TABLE IF EXISTS user_ui_settings")
        conn.executemany(
            "INSERT INTO migrations (filename) VALUES (?)",
            [(name,) for name in _DEV_ORPHANS],
        )
        conn.commit()
    finally:
        conn.close()

    setting = object.__new__(config.DatabaseSetting)
    setting.db_instance = SQLiteWrapper(str(db_path))
    setting.migrator = None

    setting.run_migrations(
        {"auto_migration": True, "migration_path": str(_MIGRATIONS_DIR)}
    )

    conn = sqlite3.connect(str(db_path))
    try:
        applied = {row[0] for row in conn.execute("SELECT filename FROM migrations")}
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    finally:
        conn.close()
    assert set(_disk_migrations()) <= applied
    assert "user_ui_settings" in tables, "089 가 이관으로 잘못 표시돼 표가 만들어지지 않았다"
    assert _provider_columns(db_path) == ["provider_id", "provider_display_name"]
