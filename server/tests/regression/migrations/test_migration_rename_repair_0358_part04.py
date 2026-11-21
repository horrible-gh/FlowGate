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

def test_every_shipped_migration_has_a_unique_slug():
    """The slug pass is only safe while one slug names exactly one migration."""
    names = _disk_migrations()
    slugs = [migration_slug(name) for name in names]
    assert len(set(slugs)) == len(names)
@pytest.mark.parametrize("history", _MOVED_HISTORIES)
def test_a_moved_file_is_paired_with_the_number_it_ran_under(history):
    disk = _disk_migrations()
    applied = (set(disk) - {_MOVED_FILE}) | {history}
    assert find_renamed_migrations(applied, disk) == [(history, _MOVED_FILE)]
def test_the_dev_deployment_ledger_pairs_the_moved_file_and_nothing_else():
    """The real ledger's shape, not a reduced one.

    Three rows point at files that no longer exist (the moved migration plus two
    unrelated leftovers from other branches) and two files are pending: the moved
    one and this group's genuinely new 089. Exactly one pair may come out, and 089
    must stay pending — recording it would leave `user_ui_settings` uncreated.
    """
    disk = _disk_migrations()
    new_file = "089_user_ui_settings.sql"
    assert new_file in disk
    applied = (set(disk) - {_MOVED_FILE, new_file}) | {
        "080b_workflow_sequence_provider.sql",
        *_DEV_ORPHANS,
    }

    assert find_renamed_migrations(applied, disk) == [
        ("080b_workflow_sequence_provider.sql", _MOVED_FILE)
    ]
def test_a_pending_file_is_left_alone_while_the_older_name_is_still_shipped():
    """Both names on disk means two migrations, not one that moved."""
    disk = ["090_thing.sql", "091_thing.sql"]
    assert find_renamed_migrations({"090_thing.sql"}, disk) == []
