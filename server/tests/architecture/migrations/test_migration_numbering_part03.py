"""Independently selectable scenarios; shared fixtures remain in test_migration_numbering.py."""
from __future__ import annotations

from tests.architecture.migrations.test_migration_numbering import (
    CHAT_COMMAND_COLUMNS,
    CHAT_COMMAND_INDEXES,
    DIALECTS,
    FILE_NAME,
    LEDGER_APPLIED_HISTORIES,
    LEDGER_FINAL_NAME,
    MAIN_CHAT_FILES,
    MIGRATIONS_DIR,
    MIGRATION_095_FINAL_NAME,
    MIGRATION_095_OLD_NAME,
    Path,
    RENAMES,
    REQUIRED_COLLISION_RENAMES,
    TRUNCATION_FILE,
    V02_CHAT_COPIES,
    WORKFLOW_PROVIDER_APPLIED_HISTORIES,
    WORKFLOW_PROVIDER_FINAL_NAME,
    _CREATE_INDEX,
    _DROPPED,
    _RENAMED_AWAY,
    _SERVER_DIR,
    _boot_like_config,
    _chat_command_shape,
    _db_from_history,
    _files,
    _fresh_real_db,
    _insert_command_row,
    _main_first_history,
    _rewind_ledger_row,
    _rewind_workflow_provider_row,
    _v02_first_history,
    _without_comments,
    apply_migration_renames,
    pytest,
    re,
    shared_migration_files,
    sqlite3,
    sys,
)

class TestFilenameCarryOver:
    """Verify filename carry-over in the migrations table itself."""

    @staticmethod
    def _make_db(tmp_path, applied: list[str] | None) -> str:
        path = str(tmp_path / "carry.db")
        conn = sqlite3.connect(path)
        try:
            if applied is not None:
                conn.execute(
                    "CREATE TABLE migrations (filename TEXT PRIMARY KEY, "
                    "applied_at DATETIME DEFAULT CURRENT_TIMESTAMP)"
                )
                conn.executemany(
                    "INSERT INTO migrations (filename) VALUES (?)",
                    [(name,) for name in applied],
                )
            else:
                conn.execute("CREATE TABLE unrelated (x INTEGER)")
            conn.commit()
        finally:
            conn.close()
        return path

    @staticmethod
    def _names(path: str) -> set[str]:
        conn = sqlite3.connect(path)
        try:
            return {r[0] for r in conn.execute("SELECT filename FROM migrations")}
        finally:
            conn.close()







    def test_real_migrator_reboot_after_carryover_adds_no_duplicate_column(self, tmp_path):
        """End-to-end proof, not a hand-rolled bookkeeping check.

        The previous revision only exercised `apply_migration_renames()` against a
        `migrations` table with no real schema behind it — so it could show the
        *ledger* rows move, but never proved that the real `sqloader.DatabaseMigrator`
        actually skips re-applying `079b_workflow_sequence_note_source.sql` afterwards.
        That is the exact gap the rejection's `duplicate column name: note` sits in:
        a ledger-only test cannot catch a migrator that still tries to re-run an
        ADD COLUMN against a column that is already there.

        This test runs the real pipeline in the same order `config.py`
        `instance_init()` does: fresh install -> simulate an already-migrated
        pre-rename database by rewriting the ledger back to the OLD filenames
        (exactly what any database migrated before this cleanup looks like right
        now) -> `apply_migration_renames()` -> construct a brand new
        `DatabaseMigrator` against the *same* on-disk `sql/migrations/sqlite`
        directory again. If the carry-over did not work, this second construction
        raises `Exception("Failed to apply migration 079b_workflow_sequence_note_source.sql: ...")`
        exactly like `sqloader.init.database_init()` does at real boot.
        """
        from sqloader import SQLiteWrapper, DatabaseMigrator

        db_path = str(tmp_path / "real_reboot.db")

        db = SQLiteWrapper(db_name=db_path)
        DatabaseMigrator(db, str(MIGRATIONS_DIR / "sqlite"), True)

        # 0408 TR0018: the ledger has one row per final name. For convergent entries
        # (078b/079b), any actual database applied only one of the two old names.
        # UPDATE rewinds only the first old name in RENAMES, reproducing that actual
        # database shape. The number of rows rewound is therefore the number of
        # distinct final names, rather than len(RENAMES).
        conn = sqlite3.connect(db_path)
        try:
            for old, new in RENAMES:
                conn.execute(
                    "UPDATE migrations SET filename = ? WHERE filename = ?", (old, new)
                )
            conn.commit()
        finally:
            conn.close()

        carried = apply_migration_renames("sqlite3", sqlite_path=db_path)
        assert carried == len({new for _, new in RENAMES})

        db2 = SQLiteWrapper(db_name=db_path)
        try:
            DatabaseMigrator(db2, str(MIGRATIONS_DIR / "sqlite"), True)
        except Exception as exc:  # pragma: no cover - failure path is the point
            pytest.fail(f"real migrator re-boot after carry-over failed: {exc}")
@pytest.mark.parametrize("dialect", DIALECTS)
def test_the_0332_ledger_sits_at_086a_in_every_dialect(dialect):
    names = {p.name for p in _files(dialect)}
    assert LEDGER_FINAL_NAME in names, f"{dialect}/{LEDGER_FINAL_NAME} 이 없다"
    for stale in LEDGER_APPLIED_HISTORIES:
        assert stale not in names, (
            f"{dialect}/{stale} 이 남아 있다. 이동은 복사가 아니다 — 옛 이름이 디스크에 "
            f"있으면 이미 이관한 DB 가 그 파일을 통째로 다시 적용한다."
        )
    # Keep both following files at their existing numbers and names.
    assert {"087_tr_commit_reapply.sql", "088_tr_conflict_session.sql"} <= names
@pytest.mark.parametrize("dialect", DIALECTS)
def test_086a_keeps_its_place_between_086_and_087(dialect):
    """The letter suffix preserves sort order.

    Moving to 089 would place this after 087/088. 086a preserves its original
    position after the parallel work's unmerged 086 and before this group's 087.
    """
    order = [p.name for p in _files(dialect)]  # _files already returns filename order.
    assert order.index(LEDGER_FINAL_NAME) < order.index("087_tr_commit_reapply.sql")
    assert order.index("087_tr_commit_reapply.sql") < order.index("088_tr_conflict_session.sql")
    # Stay after the earlier 086 when it is merged; skip this assertion if absent.
    siblings = [n for n in order if FILE_NAME.match(n).group("ordinal") == "086"]
    for sibling in siblings:
        assert sibling < LEDGER_FINAL_NAME
    # 086 and 086a are distinct ordinals, not duplicates.
    ordinals = [FILE_NAME.match(n).group("ordinal") for n in order]
    assert ordinals.count("086a") == 1
    assert ordinals.count("086") == len(siblings)
def test_both_ledger_histories_converge_directly_on_the_final_name():
    """Do not route through intermediate name 086.

    Chaining 085 -> 086 and 086 -> 086a makes a single RENAMES pass depend on
    ordering: a database with 085 might reach 086a or stop at the nonexistent
    086 file. Both historical names must point directly to the final name.
    """
    ledger_entries = {
        (old, new) for old, new in RENAMES if old in LEDGER_APPLIED_HISTORIES
    }
    assert ledger_entries == {
        (old, LEDGER_FINAL_NAME) for old in LEDGER_APPLIED_HISTORIES
    }, f"두 이력이 {LEDGER_FINAL_NAME} 로 직접 수렴하지 않는다: {sorted(ledger_entries)}"

    # Existing histories for other files remain unchanged.
    assert ("086_tr_commit_reapply.sql", "087_tr_commit_reapply.sql") in RENAMES
@pytest.mark.parametrize("history", LEDGER_APPLIED_HISTORIES)
def test_either_ledger_history_alone_is_carried_to_086a(tmp_path, history):
    """Test each history separately; neither database contains the other name."""
    path = TestFilenameCarryOver._make_db(
        tmp_path, ["001_flowgate_schema.sql", history]
    )

    carried = apply_migration_renames("sqlite3", sqlite_path=path)

    assert carried == 1, f"{history} 이력 하나만 이관돼야 한다"
    assert TestFilenameCarryOver._names(path) == {
        "001_flowgate_schema.sql",
        LEDGER_FINAL_NAME,
    }

    # A second boot carries over zero rows and leaves the ledger unchanged.
    before = TestFilenameCarryOver._names(path)
    assert apply_migration_renames("sqlite3", sqlite_path=path) == 0
    assert TestFilenameCarryOver._names(path) == before
@pytest.mark.parametrize("history", LEDGER_APPLIED_HISTORIES)
def test_real_migrator_reboot_after_ledger_carryover_does_not_rerun_the_ledger(
    tmp_path, history
):
    """Exercise a real reboot, beyond checking ledger rows.

    Follow config.py instance_init(): fresh install, rewind the ledger to the
    old name to reproduce an already-migrated database, apply_migration_renames,
    and construct DatabaseMigrator again against the same directory. A missed
    carry-over fails exactly like real boot with Failed to apply migration
    086a_tr_commit_ledger.sql: duplicate column name: last_cancel_block_reason.
    The control below demonstrates it. MySQL reruns three ADD COLUMN statements
    without IF NOT EXISTS, making the risk greater; ledger carry-over protects
    both engines.
    """
    from sqloader import SQLiteWrapper, DatabaseMigrator

    db_path = _fresh_real_db(tmp_path, f"reboot_{history[:4]}.db")
    _rewind_ledger_row(db_path, history)

    assert apply_migration_renames("sqlite3", sqlite_path=db_path) == 1
    names = TestFilenameCarryOver._names(db_path)
    assert LEDGER_FINAL_NAME in names
    assert history not in names

    try:
        DatabaseMigrator(SQLiteWrapper(db_name=db_path), str(MIGRATIONS_DIR / "sqlite"), True)
    except Exception as exc:  # pragma: no cover - failure path is the point
        pytest.fail(f"{history} 이력 DB 의 재부팅이 이관 뒤에도 실패했다: {exc}")
def test_without_the_carryover_the_ledger_really_does_rerun_and_fail(tmp_path):
    """Control: passing tests must not rely on reapplication being harmless.

    Ledger SQL uses IF NOT EXISTS for tables/indexes, but not for its three
    group_git_state ADD COLUMN statements in any dialect. Without carry-over,
    sqloader treats 086a as new and fails at the first ADD COLUMN.
    """
    from sqloader import SQLiteWrapper, DatabaseMigrator

    db_path = _fresh_real_db(tmp_path, "control.db")
    _rewind_ledger_row(db_path, LEDGER_APPLIED_HISTORIES[0])

    # Deliberately omit apply_migration_renames().
    with pytest.raises(Exception) as caught:
        DatabaseMigrator(SQLiteWrapper(db_name=db_path), str(MIGRATIONS_DIR / "sqlite"), True)

    message = str(caught.value)
    assert LEDGER_FINAL_NAME in message, message
    assert "last_cancel_block_reason" in message, message
