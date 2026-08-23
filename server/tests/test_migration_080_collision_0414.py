"""flowgate.default.0414 T0017 — the 080 ordinal collision that aborted boot in M0016.

Two parallel branches both picked 080 as their next free migration number:
0406's `080_ai_invoke_prompt_audit.sql` and 0408's
`080_workflow_sequence_provider.sql`. `server/tests/test_migration_numbering.py`
catches the ordinal collision itself; this file is the end-to-end proof that
disambiguating them (080a/080b) does not repeat the 042/062-style boot failure
sqloader's DatabaseMigrator is prone to on any rename, because it recognises an
applied migration only by its exact filename string.

The shared dev preview's database is what actually failed to boot (M0016):

    Starting Database Migrator
    Database Migration Failed.Failed to apply migration 080_workflow_sequence_provider.sql:
    duplicate column name: provider_id

Its `migrations` table records the prompt-audit migration under its own
pre-cleanup bare name (`080_ai_invoke_prompt_audit.sql`) but the provider
migration under a THIRD, differently-numbered name —
`081_workflow_sequence_provider.sql`, from before 081/082 were claimed by
document_origin_snapshot/backfill and this file was renumbered down to 080 —
while `workflow_sequence_items` already carries `provider_id` and
`provider_display_name` from whichever name it actually ran under.

That distinction matters for the fix: `080_ai_invoke_prompt_audit.sql` ->
`080a_ai_invoke_prompt_audit.sql` is a pure letter-suffix rename, which
`migration_rename_repair.reconcile_renamed_migrations` (keyed on number+slug,
ignoring a trailing letter) already carries over with no static ledger entry.
`081_workflow_sequence_provider.sql` -> `080b_workflow_sequence_provider.sql`
changes the ordinal itself (081 -> 080), which that key does not treat as equal
— only the explicit `migration_renames.RENAMES` entry this task adds prevents a
repeat of M0016.
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest

os.environ["TESTING"] = "1"
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
os.environ.setdefault("ALLOWED_ORIGIN", "*")
os.environ.setdefault("CONTEXT", "/flowgate")
os.environ.setdefault("DB_TYPE", "sqlite3")

_SERVER_DIR = Path(__file__).resolve().parents[1]
_MIGRATIONS_DIR = _SERVER_DIR / "sql" / "migrations" / "sqlite"

from modules.flow_gate.db.migration_renames import (  # noqa: E402
    RENAMES,
    apply_migration_renames,
)

_NEW_PROMPT_AUDIT = "080a_ai_invoke_prompt_audit.sql"
_NEW_PROVIDER = "080b_workflow_sequence_provider.sql"

# The two bare pre-cleanup names, plus the dev preview's third, differently
# numbered history for the provider file (module docstring above).
_BARE_PROMPT_AUDIT = "080_ai_invoke_prompt_audit.sql"
_BARE_PROVIDER = "080_workflow_sequence_provider.sql"
_OLD_NUMBERED_PROVIDER = "081_workflow_sequence_provider.sql"


def _disk_migrations() -> list[str]:
    return sorted(p.name for p in _MIGRATIONS_DIR.glob("*.sql"))


def _migrate_fresh(db_path: Path):
    from sqloader.migrator import DatabaseMigrator
    from sqloader.sqlite3 import SQLiteWrapper

    DatabaseMigrator(SQLiteWrapper(str(db_path)), str(_MIGRATIONS_DIR), auto_run=True)


def _rewrite_ledger(db_path: Path, updates: dict[str, str]) -> None:
    """updates: {current_on_disk_name: name_the_ledger_should_hold_instead}."""
    conn = sqlite3.connect(str(db_path))
    try:
        for current_name, old_name in updates.items():
            conn.execute(
                "UPDATE migrations SET filename = ? WHERE filename = ?",
                (old_name, current_name),
            )
        conn.commit()
    finally:
        conn.close()


def _plant_provider_row(db_path: Path) -> None:
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "INSERT INTO workflow_sequence_items "
            "(sequence_id, item_seq, type, label, doc_class, sort_order, provider_id, "
            "provider_display_name) VALUES (1, 1, 'T', 'Test step', 'T', 1, "
            "'anthropic:sonnet', 'Sonnet 5')"
        )
        conn.commit()
    finally:
        conn.close()


def _provider_columns(db_path: Path) -> set[str]:
    conn = sqlite3.connect(str(db_path))
    try:
        return {row[1] for row in conn.execute("PRAGMA table_info(workflow_sequence_items)")}
    finally:
        conn.close()


def _dev_preview_state_db(tmp_path: Path, name: str) -> Path:
    """A fully migrated DB, then aged to the exact ledger the dev preview held.

    `080_ai_invoke_prompt_audit.sql` recorded under its own pre-cleanup bare name,
    `080_workflow_sequence_provider.sql` recorded under the older
    `081_workflow_sequence_provider.sql` it actually ran under. A live
    `workflow_sequence_items` row with provider data set is planted so the test can
    prove the row survives the repair unchanged.
    """
    db_path = tmp_path / name
    _migrate_fresh(db_path)
    _rewrite_ledger(
        db_path,
        {
            _NEW_PROMPT_AUDIT: _BARE_PROMPT_AUDIT,
            _NEW_PROVIDER: _OLD_NUMBERED_PROVIDER,
        },
    )
    _plant_provider_row(db_path)
    return db_path


# ── the ledger entries this collision requires ───────────────────────────────


def test_the_080_renames_are_in_the_ledger():
    required = {
        (_BARE_PROMPT_AUDIT, _NEW_PROMPT_AUDIT),
        (_BARE_PROVIDER, _NEW_PROVIDER),
        (_OLD_NUMBERED_PROVIDER, _NEW_PROVIDER),
    }
    assert required <= set(RENAMES)


def test_080a_and_080b_are_on_disk_and_the_bare_names_are_gone():
    disk = set(_disk_migrations())
    assert {_NEW_PROMPT_AUDIT, _NEW_PROVIDER} <= disk
    assert _BARE_PROMPT_AUDIT not in disk
    assert _BARE_PROVIDER not in disk


# ── the rejected boot, reproduced against the real migrator ──────────────────


def test_the_rejected_boot_failure_reproduces_without_the_081_rename(tmp_path):
    """Dynamic letter-suffix reconciliation alone rescues 080a but not 080b.

    Runs the same two-step repair `config.py` `run_migrations` runs
    (`reconcile_renamed_migrations`, the generic number+slug-keyed one) WITHOUT
    first carrying over the static `RENAMES` ledger — i.e. the state of the
    world before this task added the 081 -> 080b entry. 080a is rescued for
    free; 080b is not, and the boot fails exactly as M0016 recorded it.
    """
    from migration_rename_repair import reconcile_renamed_migrations
    from sqloader.migrator import DatabaseMigrator
    from sqloader.sqlite3 import SQLiteWrapper

    db_path = _dev_preview_state_db(tmp_path, "unrepaired.db")

    db = SQLiteWrapper(str(db_path))
    migrator = DatabaseMigrator(db, str(_MIGRATIONS_DIR), auto_run=False)
    reconcile_renamed_migrations(db, str(_MIGRATIONS_DIR))

    with pytest.raises(Exception) as excinfo:
        migrator.apply_migrations()

    message = str(excinfo.value)
    assert _NEW_PROVIDER in message
    assert "duplicate column" in message.lower()
    assert "provider_id" in message


@pytest.mark.parametrize(
    "old_provider_name",
    [_BARE_PROVIDER, _OLD_NUMBERED_PROVIDER],
    ids=["bare-080-history", "renumbered-081-history"],
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
    # pending for either 080a or 080b.
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


def test_a_db_already_cleaned_up_to_080a_080b_is_untouched(tmp_path):
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


def test_a_brand_new_database_applies_every_sqlite_migration_including_080a_080b(tmp_path):
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
