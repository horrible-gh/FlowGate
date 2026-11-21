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

@pytest.mark.parametrize(
    "old_provider_name",
    list(_PROVIDER_HISTORIES),
    ids=["bare-080-history", "renumbered-081-history", "lettered-080b-history"],
)
def test_the_dynamic_pass_alone_now_carries_every_provider_history(
    tmp_path, old_provider_name
):
    """M0016's boot failure no longer reproduces from any history, and why.

    T0017 wrote this as the control proving the static `RENAMES` entry was
    load-bearing: `reconcile_renamed_migrations` keyed on number+slug alone, so a
    history whose ordinal differed from the on-disk name went unrescued and the
    boot died on the already-present column. 0452 added a second pass keyed on the
    slug with the ordinal dropped, which reaches exactly those cases, so the
    dynamic repair now carries all three histories on its own.

    What that second pass requires is the invariant this file's disk test guards:
    the already-applied name must be **gone from disk**. Ship both names and they
    are two different migrations again, the pending one runs, and M0016 comes
    back — which is what merging 0414 and 0452 did by leaving 080b beside 081a.
    """
    from migration_rename_repair import reconcile_renamed_migrations
    from sqloader.migrator import DatabaseMigrator
    from sqloader.sqlite3 import SQLiteWrapper

    db_path = tmp_path / f"dynamic-only-{old_provider_name}.db"
    _migrate_fresh(db_path)
    _rewrite_ledger(
        db_path,
        {_NEW_PROMPT_AUDIT: _BARE_PROMPT_AUDIT, _NEW_PROVIDER: old_provider_name},
    )
    _plant_provider_row(db_path)
    before = _provider_columns(db_path)

    db = SQLiteWrapper(str(db_path))
    migrator = DatabaseMigrator(db, str(_MIGRATIONS_DIR), auto_run=False)
    carried = dict(reconcile_renamed_migrations(db, str(_MIGRATIONS_DIR)))
    assert carried.get(old_provider_name) == _NEW_PROVIDER

    migrator.apply_migrations()  # must not raise

    assert _provider_columns(db_path) == before
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute(
            "SELECT provider_id, provider_display_name FROM workflow_sequence_items "
            "WHERE sequence_id = 1 AND item_seq = 1"
        ).fetchone()
    finally:
        conn.close()
    assert row == ("anthropic:sonnet", "Sonnet 5")
@pytest.mark.parametrize(
    "old_provider_name",
    list(_PROVIDER_HISTORIES),
    ids=["bare-080-history", "renumbered-081-history", "lettered-080b-history"],
)
def test_repair_lets_the_same_boot_finish_without_losing_a_column_or_row(
    tmp_path, old_provider_name
):
    """Both observed histories for the provider file carry over cleanly."""
    from sqloader.migrator import DatabaseMigrator
    from sqloader.sqlite3 import SQLiteWrapper

    db_path = tmp_path / f"repaired-{old_provider_name}.db"
    _migrate_fresh(db_path)
    _rewrite_ledger(
        db_path,
        {_NEW_PROMPT_AUDIT: _BARE_PROMPT_AUDIT, _NEW_PROVIDER: old_provider_name},
    )
    _plant_provider_row(db_path)
    before = _provider_columns(db_path)

    carried = apply_migration_renames("sqlite3", sqlite_path=str(db_path))
    assert carried >= 2

    # Should not raise: the real migrator, constructed fresh, must see nothing
    # pending for either 080a or the provider file.
    DatabaseMigrator(SQLiteWrapper(str(db_path)), str(_MIGRATIONS_DIR), auto_run=True)

    assert _provider_columns(db_path) == before
    assert {"provider_id", "provider_display_name"} <= before

    conn = sqlite3.connect(str(db_path))
    try:
        applied = {row[0] for row in conn.execute("SELECT filename FROM migrations")}
        assert {_NEW_PROMPT_AUDIT, _NEW_PROVIDER} <= applied
        assert _BARE_PROMPT_AUDIT not in applied
        assert old_provider_name not in applied

        row = conn.execute(
            "SELECT provider_id, provider_display_name FROM workflow_sequence_items "
            "WHERE sequence_id = 1 AND item_seq = 1"
        ).fetchone()
        assert row == ("anthropic:sonnet", "Sonnet 5")
    finally:
        conn.close()
