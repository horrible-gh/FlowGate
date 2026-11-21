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

def test_repair_is_idempotent_across_boots(tmp_path):
    from sqloader.sqlite3 import SQLiteWrapper

    db_path = _fully_migrated_db(tmp_path)
    db = SQLiteWrapper(str(db_path))
    assert len(reconcile_renamed_migrations(db, str(_MIGRATIONS_DIR))) == len(_RENAMED)
    assert reconcile_renamed_migrations(db, str(_MIGRATIONS_DIR)) == []
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
def test_a_slug_survives_the_move_that_a_number_key_cannot_follow():
    for history in _MOVED_HISTORIES:
        assert migration_slug(history) == migration_slug(_MOVED_FILE)
    # The letter-only key is blind to two of the three — that blindness is the bug.
    assert migration_key("081_workflow_sequence_provider.sql") == migration_key(_MOVED_FILE)
    assert migration_key("080b_workflow_sequence_provider.sql") != migration_key(_MOVED_FILE)
    # A different migration that merely sits on the same ordinal keeps its own slug.
    assert migration_slug("081_document_origin_snapshot.sql") != migration_slug(_MOVED_FILE)
