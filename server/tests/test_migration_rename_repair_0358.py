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

_SERVER_DIR = Path(__file__).resolve().parents[1]
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


def test_renamed_files_are_paired_with_the_name_they_ran_under():
    disk = _disk_migrations()
    applied = (set(disk) - set(_RENAMED.values())) | set(_RENAMED)
    pairs = find_renamed_migrations(applied, disk)
    assert pairs == sorted((old, new) for old, new in _RENAMED.items())


# ── the rejected boot, reproduced and then fixed ─────────────────────────────


def test_the_rejected_boot_failure_reproduces_without_the_repair(tmp_path):
    from sqloader.migrator import DatabaseMigrator
    from sqloader.sqlite3 import SQLiteWrapper

    db_path = _fully_migrated_db(tmp_path)

    with pytest.raises(Exception) as excinfo:
        DatabaseMigrator(SQLiteWrapper(str(db_path)), str(_MIGRATIONS_DIR), auto_run=True)

    message = str(excinfo.value)
    assert _RENAMED["042_tokens_review_scope.sql"] in message, message
    assert "CHECK constraint failed" in message, message


def test_repair_lets_the_same_boot_finish_without_losing_a_column_or_row(tmp_path):
    from sqloader.migrator import DatabaseMigrator
    from sqloader.sqlite3 import SQLiteWrapper

    db_path = _fully_migrated_db(tmp_path)
    before = _token_columns(db_path)

    db = SQLiteWrapper(str(db_path))
    migrator = DatabaseMigrator(db, str(_MIGRATIONS_DIR), auto_run=False)
    repaired = reconcile_renamed_migrations(db, str(_MIGRATIONS_DIR))
    migrator.apply_migrations()

    assert sorted(new for _old, new in repaired) == sorted(_RENAMED.values())

    conn = sqlite3.connect(str(db_path))
    try:
        applied = {row[0] for row in conn.execute("SELECT filename FROM migrations")}
        # Every file on disk is now accounted for, so the next boot applies nothing.
        assert set(_disk_migrations()) <= applied
        # 042/062 rebuild tokens with their own era's columns; a second run would
        # have dropped everything added after them.
        assert _token_columns(db_path) == before
        assert {"dry_run_count", "merge_id", "continuation_locale"} <= before
        row = conn.execute(
            "SELECT action_scope FROM tokens WHERE token_id = 'tok_live'"
        ).fetchone()
        assert row is not None and row[0] == "resolve_conflict"
    finally:
        conn.close()


def test_repair_writes_nothing_when_the_names_already_line_up(tmp_path):
    from sqloader.migrator import DatabaseMigrator
    from sqloader.sqlite3 import SQLiteWrapper

    db_path = tmp_path / "healthy.db"
    db = SQLiteWrapper(str(db_path))
    DatabaseMigrator(db, str(_MIGRATIONS_DIR), auto_run=True)

    assert reconcile_renamed_migrations(db, str(_MIGRATIONS_DIR)) == []

    conn = sqlite3.connect(str(db_path))
    try:
        applied = [row[0] for row in conn.execute("SELECT filename FROM migrations")]
        assert sorted(applied) == _disk_migrations()
    finally:
        conn.close()


def test_repair_is_idempotent_across_boots(tmp_path):
    from sqloader.sqlite3 import SQLiteWrapper

    db_path = _fully_migrated_db(tmp_path)
    db = SQLiteWrapper(str(db_path))
    assert len(reconcile_renamed_migrations(db, str(_MIGRATIONS_DIR))) == len(_RENAMED)
    assert reconcile_renamed_migrations(db, str(_MIGRATIONS_DIR)) == []


# ── the boot path that actually runs it ──────────────────────────────────────


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


def test_a_slug_survives_the_move_that_a_number_key_cannot_follow():
    for history in _MOVED_HISTORIES:
        assert migration_slug(history) == migration_slug(_MOVED_FILE)
    # The letter-only key is blind to two of the three — that blindness is the bug.
    assert migration_key("081_workflow_sequence_provider.sql") == migration_key(_MOVED_FILE)
    assert migration_key("080b_workflow_sequence_provider.sql") != migration_key(_MOVED_FILE)
    # A different migration that merely sits on the same ordinal keeps its own slug.
    assert migration_slug("081_document_origin_snapshot.sql") != migration_slug(_MOVED_FILE)


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
