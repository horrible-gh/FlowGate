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

@pytest.mark.parametrize("dialect", DIALECTS)
def test_the_081_workflow_sequence_provider_collision_sits_at_081a_in_every_dialect(dialect):
    names = {p.name for p in _files(dialect)}
    assert WORKFLOW_PROVIDER_FINAL_NAME in names, f"{dialect}/{WORKFLOW_PROVIDER_FINAL_NAME} 이 없다"
    for stale in WORKFLOW_PROVIDER_APPLIED_HISTORIES:
        assert stale not in names, (
            f"{dialect}/{stale} 이 되살아났다. 이동은 복사가 아니다 — 옛 이름이 디스크에 "
            f"있으면 이미 이관한 DB 가 그 파일을 통째로 다시 적용한다."
        )
    # This move resolves the collision with 081_document_origin_snapshot.sql;
    # that file and the following 082 must keep their numbers and names.
    assert {"081_document_origin_snapshot.sql", "082_document_origin_backfill.sql"} <= names
@pytest.mark.parametrize("dialect", DIALECTS)
def test_081a_keeps_its_place_between_081_and_082(dialect):
    order = [p.name for p in _files(dialect)]  # _files already returns filename order.
    assert order.index("081_document_origin_snapshot.sql") < order.index(WORKFLOW_PROVIDER_FINAL_NAME)
    assert order.index(WORKFLOW_PROVIDER_FINAL_NAME) < order.index("082_document_origin_backfill.sql")
    # 081 and 081a are distinct ordinals, not duplicates.
    ordinals = [FILE_NAME.match(n).group("ordinal") for n in order]
    assert ordinals.count("081a") == 1
    assert ordinals.count("081") == 1
def test_both_workflow_sequence_provider_histories_converge_directly_on_081a():
    """Avoid intermediate name 081 for the same reason as the 086a case."""
    entries = {
        (old, new) for old, new in RENAMES if old in WORKFLOW_PROVIDER_APPLIED_HISTORIES
    }
    assert entries == {
        (old, WORKFLOW_PROVIDER_FINAL_NAME) for old in WORKFLOW_PROVIDER_APPLIED_HISTORIES
    }, f"두 이력이 {WORKFLOW_PROVIDER_FINAL_NAME} 로 직접 수렴하지 않는다: {sorted(entries)}"
@pytest.mark.parametrize("history", WORKFLOW_PROVIDER_APPLIED_HISTORIES)
def test_either_workflow_sequence_provider_history_alone_is_carried_to_081a(tmp_path, history):
    """Test each history separately; neither database contains the other name."""
    path = TestFilenameCarryOver._make_db(
        tmp_path, ["001_flowgate_schema.sql", history]
    )

    carried = apply_migration_renames("sqlite3", sqlite_path=path)

    assert carried == 1, f"{history} 이력 하나만 이관돼야 한다"
    assert TestFilenameCarryOver._names(path) == {
        "001_flowgate_schema.sql",
        WORKFLOW_PROVIDER_FINAL_NAME,
    }

    before = TestFilenameCarryOver._names(path)
    assert apply_migration_renames("sqlite3", sqlite_path=path) == 0
    assert TestFilenameCarryOver._names(path) == before
@pytest.mark.parametrize("history", WORKFLOW_PROVIDER_APPLIED_HISTORIES)
def test_real_migrator_reboot_after_workflow_sequence_provider_carryover_does_not_rerun_it(
    tmp_path, history
):
    """Reproduce the rejection through a real reboot, beyond ledger inspection.

    Follow config.py instance_init(): fresh install, rewind to a historical
    ledger name, apply_migration_renames, and construct DatabaseMigrator again
    against the same directory. Missed carry-over fails with Failed to apply
    migration 081a_workflow_sequence_provider.sql: duplicate column name:
    provider_id, matching the original 081_workflow_sequence_provider.sql
    rejection. The control below demonstrates that failure.
    """
    from sqloader import SQLiteWrapper, DatabaseMigrator

    db_path = _fresh_real_db(tmp_path, f"wf_provider_reboot_{history[:4]}.db")
    _rewind_workflow_provider_row(db_path, history)

    assert apply_migration_renames("sqlite3", sqlite_path=db_path) == 1
    names = TestFilenameCarryOver._names(db_path)
    assert WORKFLOW_PROVIDER_FINAL_NAME in names
    assert history not in names

    try:
        DatabaseMigrator(SQLiteWrapper(db_name=db_path), str(MIGRATIONS_DIR / "sqlite"), True)
    except Exception as exc:  # pragma: no cover - failure path is the point
        pytest.fail(f"{history} 이력 DB 의 재부팅이 이관 뒤에도 실패했다: {exc}")
def test_without_the_carryover_workflow_sequence_provider_really_does_rerun_and_fail(tmp_path):
    """Control: actually reproduce duplicate column name: provider_id.

    Both 081/080 add provider_id/provider_display_name without IF NOT EXISTS.
    Without carry-over, sqloader treats 081a as new and reproduces the rejection
    at the first ADD COLUMN.
    """
    from sqloader import SQLiteWrapper, DatabaseMigrator

    db_path = _fresh_real_db(tmp_path, "wf_provider_control.db")
    _rewind_workflow_provider_row(db_path, WORKFLOW_PROVIDER_APPLIED_HISTORIES[1])

    # Deliberately omit apply_migration_renames().
    with pytest.raises(Exception) as caught:
        DatabaseMigrator(SQLiteWrapper(db_name=db_path), str(MIGRATIONS_DIR / "sqlite"), True)

    message = str(caught.value)
    assert WORKFLOW_PROVIDER_FINAL_NAME in message, message
    assert "duplicate column name: provider_id" in message, message
def test_the_095_rename_is_in_the_ledger():
    assert (MIGRATION_095_OLD_NAME, MIGRATION_095_FINAL_NAME) in RENAMES
