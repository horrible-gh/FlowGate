"""flowgate.default.0358 R0001 4번째 반려 — a renamed migration must not run twice.

The rejected boot log is one line:

    Database Migration Failed.Failed to apply migration 042_tokens_review_scope.sql:
    CHECK constraint failed: action_scope IN ('new', 'edit', 'workflow_decide', 'review')

sqloader's DatabaseMigrator recognises an applied migration only by its exact
filename, so a database that recorded `042a_tokens_review_scope.sql` treats today's
`042_tokens_review_scope.sql` as pending and executes it again — against a tokens
table that six later migrations have widened.

These tests reproduce that boot failure on a real sqlite database through the real
migrator (not a stand-in), then prove the repair in server/migration_rename_repair.py
lets the same boot finish without losing a column or a row, and that it never hides a
migration that genuinely has not run.
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest

# `import config` instantiates Settings() at import time, so the required keys must
# be in the environment before then — same preamble as
# tests/test_pool_config_and_auth_load_0288.py, which imports config for the same
# reason. TESTING=1 keeps that import from building a real DatabaseSetting.
os.environ["TESTING"] = "1"
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
os.environ.setdefault("ALLOWED_ORIGIN", "*")
os.environ.setdefault("CONTEXT", "/flowgate")
os.environ.setdefault("DB_TYPE", "sqlite3")

from migration_rename_repair import (
    find_renamed_migrations,
    migration_key,
    migration_slug,
    reconcile_renamed_migrations,
)
from modules.flow_gate.db.migration_renames import RENAMES as _RENAMES

_SERVER_DIR = Path(__file__).resolve().parents[3]
_MIGRATIONS_DIR = _SERVER_DIR / "sql" / "migrations" / "sqlite"

# What an already-migrated database holds for the six files whose number collided
# with a parallel branch and were later disambiguated. Left column: the name that
# database recorded. Right column: the name shipped today.
#
# flowgate.default.0452: this dict pointed the other way — letter on the left, bare number
# on the right — which was true when 0358 wrote it and stopped being true when 0394
# T0004 settled the collisions the opposite way, giving the letter to the file that
# is shipped. Nothing failed loudly at the time; the four tests below simply started
# building a database whose ledger already matched disk, so they asserted against an
# empty repair. The direction is now read from `migration_renames.RENAMES`, which is
# the same table the boot path uses, and `test_the_rename_fixture_matches_disk` keeps
# it from drifting again. Only the letter-suffix pairs belong here — a file that moved
# to a different NUMBER is the second failure mode, exercised further down.
def _letter_suffix_pairs() -> dict[str, str]:
    seen: dict[str, str] = {}
    for old, new in _RENAMES:
        if migration_key(old) != migration_key(new):
            continue  # a move to another number — the second failure mode, below
        # RENAMES may list several recorded spellings converging on one shipped
        # file (two branches lettered the same collision differently). No single
        # database holds more than one of them, so the fixture takes one.
        seen.setdefault(new, old)
    return {old: new for new, old in seen.items()}


_RENAMED = _letter_suffix_pairs()


def _disk_migrations() -> list[str]:
    return sorted(p.name for p in _MIGRATIONS_DIR.glob("*.sql"))


def test_the_rename_fixture_matches_disk():
    """The fixture is only a reproduction while it disagrees with disk in the real direction.

    A stale direction here is silent: every test still runs, it just builds a
    database that needs no repair and then checks that no repair happened.
    """
    disk = set(_disk_migrations())
    assert _RENAMED, "letter-suffix rename pairs disappeared from RENAMES"
    for recorded, shipped in _RENAMED.items():
        assert shipped in disk, f"{shipped} 이 디스크에 없다 — 오른쪽이 오늘 배포되는 이름이어야 한다"
        assert recorded not in disk, f"{recorded} 이 디스크에 있다 — 왼쪽은 사라진 옛 이름이어야 한다"


def _fully_migrated_db(tmp_path: Path) -> Path:
    """A database migrated the way the server migrates it, then aged.

    Rewrites the six bookkeeping rows to the older filenames and leaves a token row
    whose action_scope only exists because later migrations widened the CHECK — the
    exact combination that made the deployment's boot fail.
    """
    from sqloader.migrator import DatabaseMigrator
    from sqloader.sqlite3 import SQLiteWrapper

    db_path = tmp_path / "deployment.db"
    DatabaseMigrator(SQLiteWrapper(str(db_path)), str(_MIGRATIONS_DIR), auto_run=True)

    conn = sqlite3.connect(str(db_path))
    try:
        for old_name, current_name in _RENAMED.items():
            conn.execute(
                "UPDATE migrations SET filename = ? WHERE filename = ?",
                (old_name, current_name),
            )
        conn.execute(
            "INSERT INTO tokens (token_id, hash, pepper_id, project, doc_ref, "
            "action_scope, issued_to, created_at, expires_at) VALUES "
            "('tok_live', 'h', 'p1', 'flowgate', 'flowgate.default.0358.0005-TR', "
            "'resolve_conflict', 'u1', '2026-08-16T00:00:00', '2026-08-17T00:00:00')"
        )
        conn.commit()
    finally:
        conn.close()
    return db_path


def _token_columns(db_path: Path) -> set[str]:
    conn = sqlite3.connect(str(db_path))
    try:
        return {row[1] for row in conn.execute("PRAGMA table_info(tokens)")}
    finally:
        conn.close()


# ── the key: same number + same slug, nothing looser ────────────────────────


def test_letter_suffix_is_the_only_difference_a_key_ignores():
    assert migration_key("042a_tokens_review_scope.sql") == migration_key(
        "042_tokens_review_scope.sql"
    )
    # Same number, different migration — these must never be treated as one file.
    assert migration_key("042_project_messages.sql") != migration_key(
        "042_tokens_review_scope.sql"
    )
    # A number that is not a rename of anything keeps its own identity.
    assert migration_key("074_test_run_cancel_status.sql") != migration_key(
        "074a_ai_invoke_chain_progress.sql"
    )


def test_every_shipped_migration_has_a_unique_key():
    """A duplicate key would let one file mark an unrelated one as applied."""
    names = _disk_migrations()
    keys = [migration_key(name) for name in names]
    assert len(set(keys)) == len(names)


def test_pending_migrations_without_an_applied_sibling_are_left_alone():
    """Genuinely new files must stay pending — the repair may not mask them."""
    disk = _disk_migrations()
    applied = set(disk) - {
        "074_test_run_cancel_status.sql",
        "075_test_run_cases_fk_repair.sql",
    }
    assert find_renamed_migrations(applied, disk) == []




# ── the rejected boot, reproduced and then fixed ─────────────────────────────










# ── the boot path that actually runs it ──────────────────────────────────────






# ── 0452: the file moved to another NUMBER, not just another letter ──────────
#
# The rejected boot log, twice in a row, one revision apart:
#
#     Starting Database Migrator
#     Database Migration Failed.Failed to apply migration
#     081_workflow_sequence_provider.sql: duplicate column name: provider_id
#     ...
#     Database Migration Failed.Failed to apply migration
#     081a_workflow_sequence_provider.sql: duplicate column name: provider_id
#
# Same file, three spellings. `migration_key` collapses only the letter suffix, so
# it pairs 042a with 042 but cannot pair 080b with 081a — and the number is exactly
# what a *moved* file changes. The dev deployment's ledger holds the third spelling,
# 080b_workflow_sequence_provider.sql, which no hand-written rename table had ever
# seen; that is why fixing the first rejection by hand produced the second one.

_MOVED_FILE = "081a_workflow_sequence_provider.sql"
_MOVED_HISTORIES = (
    "080_workflow_sequence_provider.sql",   # before 0413 T0007 moved it off 080
    "080b_workflow_sequence_provider.sql",  # the dev deployment's actual ledger row
    "081_workflow_sequence_provider.sql",   # after T0007, before this fix (stg holds this)
)
# Rows in that same dev ledger whose file is gone from every dialect directory.
# They must stay untouched: nothing pending shares their slug.
_DEV_ORPHANS = ("080a_ai_invoke_prompt_audit.sql", "086_ai_invoke_paused_review.sql")














def _migrated_db(tmp_path: Path, name: str) -> Path:
    from sqloader.migrator import DatabaseMigrator
    from sqloader.sqlite3 import SQLiteWrapper

    db_path = tmp_path / name
    DatabaseMigrator(SQLiteWrapper(str(db_path)), str(_MIGRATIONS_DIR), auto_run=True)
    return db_path


def _rewind_moved_row(db_path: Path, history: str) -> None:
    conn = sqlite3.connect(str(db_path))
    try:
        cursor = conn.execute(
            "UPDATE migrations SET filename = ? WHERE filename = ?",
            (history, _MOVED_FILE),
        )
        assert cursor.rowcount == 1, f"{_MOVED_FILE} 행이 장부에 없다"
        conn.commit()
    finally:
        conn.close()


def _provider_columns(db_path: Path) -> list[str]:
    conn = sqlite3.connect(str(db_path))
    try:
        return [
            row[1]
            for row in conn.execute("PRAGMA table_info(workflow_sequence_items)")
            if row[1].startswith("provider_")
        ]
    finally:
        conn.close()
