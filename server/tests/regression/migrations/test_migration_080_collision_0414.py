"""flowgate.default.0414 T0017 — the 080 ordinal collision that aborted boot in M0016.

Two parallel branches both picked 080 as their next free migration number:
0406's `080_ai_invoke_prompt_audit.sql` and 0408's
`080_workflow_sequence_provider.sql`. `server/tests/architecture/migrations/test_migration_numbering.py`
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

_SERVER_DIR = Path(__file__).resolve().parents[3]
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
