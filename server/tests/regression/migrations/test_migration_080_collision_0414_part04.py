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

def test_a_brand_new_database_applies_every_sqlite_migration_including_080a_081a(tmp_path):
    db_path = tmp_path / "fresh.db"
    _migrate_fresh(db_path)

    conn = sqlite3.connect(str(db_path))
    try:
        applied = [row[0] for row in conn.execute("SELECT filename FROM migrations")]
    finally:
        conn.close()

    # Applied exactly once each, and the full set matches what is on disk today —
    # nothing was skipped and nothing repeated.
    assert applied.count(_NEW_PROMPT_AUDIT) == 1
    assert applied.count(_NEW_PROVIDER) == 1
    assert sorted(applied) == _disk_migrations()
def test_config_run_migrations_repairs_the_dev_preview_state_before_it_applies(tmp_path):
    """The exact boot order config.py drives: static carry-over, then run_migrations."""
    import config
    from sqloader.sqlite3 import SQLiteWrapper

    db_path = _dev_preview_state_db(tmp_path, "config_boot.db")

    apply_migration_renames("sqlite3", sqlite_path=str(db_path))

    setting = object.__new__(config.DatabaseSetting)
    setting.db_instance = SQLiteWrapper(str(db_path))
    setting.migrator = None

    setting.run_migrations({"auto_migration": True, "migration_path": str(_MIGRATIONS_DIR)})

    assert setting.migrator is not None
    conn = sqlite3.connect(str(db_path))
    try:
        applied = {row[0] for row in conn.execute("SELECT filename FROM migrations")}
        row = conn.execute(
            "SELECT provider_id FROM workflow_sequence_items "
            "WHERE sequence_id = 1 AND item_seq = 1"
        ).fetchone()
    finally:
        conn.close()
    assert {_NEW_PROMPT_AUDIT, _NEW_PROVIDER} <= applied
    assert row == ("anthropic:sonnet",)
