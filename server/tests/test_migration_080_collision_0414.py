"""flowgate.default.0414 T0017 — the 080 ordinal collision that aborted boot in M0016.

Two parallel branches both picked 080 as their next free migration number:
0406's `080_ai_invoke_prompt_audit.sql` and 0408's
`080_workflow_sequence_provider.sql`. `server/tests/test_migration_numbering.py`
catches the ordinal collision itself; this file is the end-to-end proof that
disambiguating them (080a/080b) did not repeat the 042/062-style boot failure
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
`081_workflow_sequence_provider.sql` -> `081a_workflow_sequence_provider.sql`
changes the ordinal itself, which that key does not treat as equal — only the
explicit `migration_renames.RENAMES` entry this task adds prevents a repeat of
M0016.

flowgate.default.0452 moved the provider file once more. T0017 had parked it at
`080b_workflow_sequence_provider.sql`; 0452 found that 081 — the ordinal T0007
had originally moved it to — was independently claimed by 0410's
`081_document_origin_snapshot.sql`, and settled the file at
`081a_workflow_sequence_provider.sql`, keeping its place between 081 and 082.
`080b_` is therefore a *third* already-applied history, not a destination, and
this file tracks the same three histories `test_migration_numbering.py` §9 does.
Merging 0414 and 0452 resurrected `080b_…sql` on disk beside `081a_…sql` — two
files carrying the identical DDL — and the migrator ran the one with no ledger
row, aborting the boot with `column "provider_id" … already exists`. A move is
not a copy: only the final name may exist on disk.
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
_NEW_PROVIDER = "081a_workflow_sequence_provider.sql"

# The two bare pre-cleanup names, plus the provider file's two further histories
# (module docstring above): the ordinal T0007 moved it to, and the letter suffix
# T0017 parked it at before 0452 settled it at 081a.
_BARE_PROMPT_AUDIT = "080_ai_invoke_prompt_audit.sql"
_BARE_PROVIDER = "080_workflow_sequence_provider.sql"
_OLD_NUMBERED_PROVIDER = "081_workflow_sequence_provider.sql"
_OLD_LETTERED_PROVIDER = "080b_workflow_sequence_provider.sql"

# Every name the provider migration has ever been applied under. None of them may
# survive on disk — `test_migration_numbering.py` §9 guards the same list.
_PROVIDER_HISTORIES = (_BARE_PROVIDER, _OLD_NUMBERED_PROVIDER, _OLD_LETTERED_PROVIDER)


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
    required = {(_BARE_PROMPT_AUDIT, _NEW_PROMPT_AUDIT)}
    required |= {(old, _NEW_PROVIDER) for old in _PROVIDER_HISTORIES}
    assert required <= set(RENAMES)


def test_080a_and_081a_are_on_disk_and_every_older_name_is_gone():
    """A move is not a copy — an older name left on disk gets applied a second time."""
    disk = set(_disk_migrations())
    assert {_NEW_PROMPT_AUDIT, _NEW_PROVIDER} <= disk
    assert _BARE_PROMPT_AUDIT not in disk
    for stale in _PROVIDER_HISTORIES:
        assert stale not in disk, (
            f"{stale} is back on disk beside {_NEW_PROVIDER}. Both carry the same DDL, "
            f"and the migrator applies whichever one has no ledger row."
        )


# ── the rejected boot, reproduced against the real migrator ──────────────────


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
