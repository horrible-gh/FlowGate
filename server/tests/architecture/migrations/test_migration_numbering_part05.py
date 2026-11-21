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
    """`migrations` 표의 파일 이름을 옮기는 동작 자체를 확인한다."""

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

def test_real_migrator_reboot_after_095_carryover_does_not_rerun_it(tmp_path):
    """반려 사유("duplicate column name: operator_api_base")가 이관 뒤 사라지는가."""
    from sqloader import SQLiteWrapper, DatabaseMigrator

    db_path = _fresh_real_db(tmp_path, "reboot_095.db")
    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.execute(
            "UPDATE migrations SET filename = ? WHERE filename = ?",
            (MIGRATION_095_OLD_NAME, MIGRATION_095_FINAL_NAME),
        )
        assert cursor.rowcount == 1, (
            f"{MIGRATION_095_FINAL_NAME} 행이 장부에 없다 — 파일 이름이 바뀌었거나 "
            f"마이그레이션이 적용되지 않았다."
        )
        conn.commit()
    finally:
        conn.close()

    assert apply_migration_renames("sqlite3", sqlite_path=db_path) == 1
    names = TestFilenameCarryOver._names(db_path)
    assert MIGRATION_095_FINAL_NAME in names
    assert MIGRATION_095_OLD_NAME not in names

    try:
        DatabaseMigrator(SQLiteWrapper(db_name=db_path), str(MIGRATIONS_DIR / "sqlite"), True)
    except Exception as exc:  # pragma: no cover - failure path is the point
        pytest.fail(f"095 이력 DB 의 재부팅이 이관 뒤에도 실패했다: {exc}")
def test_without_the_095_carryover_it_really_does_rerun_and_fail(tmp_path):
    """대조군. 반려 사유가 실제로 재현되는가."""
    from sqloader import SQLiteWrapper, DatabaseMigrator

    db_path = _fresh_real_db(tmp_path, "control_095.db")
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "UPDATE migrations SET filename = ? WHERE filename = ?",
            (MIGRATION_095_OLD_NAME, MIGRATION_095_FINAL_NAME),
        )
        conn.commit()
    finally:
        conn.close()

    # apply_migration_renames() 를 일부러 호출하지 않는다.
    with pytest.raises(Exception) as caught:
        DatabaseMigrator(SQLiteWrapper(db_name=db_path), str(MIGRATIONS_DIR / "sqlite"), True)

    message = str(caught.value)
    assert MIGRATION_095_FINAL_NAME in message, message
    assert "duplicate column name: operator_api_base" in message, message
@pytest.mark.parametrize("dialect", DIALECTS)
def test_the_v02_copies_of_main_chat_migrations_are_gone(dialect):
    names = {p.name for p in _files(dialect)}
    assert set(MAIN_CHAT_FILES) | {TRUNCATION_FILE} <= names
    revived = sorted(set(V02_CHAT_COPIES.values()) & names)
    assert revived == [], (
        f"{dialect}: v0.2 사본 {revived} 이 되살아났다. main 의 136/137 과 같은 표·인덱스·컬럼을 "
        f"다시 만들어 부팅이 idx_chat_cmd_run already exists 로 선다."
    )
def test_a_fresh_db_gets_the_whole_chat_command_shape_and_reboots(tmp_path):
    db_path = _fresh_real_db(tmp_path, "chat_fresh.db")
    assert _boot_like_config(db_path) == []
    columns, indexes, _rows, applied = _chat_command_shape(db_path)
    assert columns == CHAT_COMMAND_COLUMNS
    assert indexes == CHAT_COMMAND_INDEXES
    assert {*MAIN_CHAT_FILES, TRUNCATION_FILE} <= applied
def test_a_main_first_db_boots_v02_and_keeps_its_rows(tmp_path):
    """STG 이력. 이 DB 에서 v0.2 부팅이 idx_chat_cmd_run already exists 로 섰다."""
    db_path = _db_from_history(tmp_path, "chat_main_first", _main_first_history())
    _insert_command_row(db_path)

    assert _boot_like_config(db_path) == [], "main 이력에는 옮겨 적을 이름이 없다"

    columns, indexes, rows, applied = _chat_command_shape(db_path)
    assert columns == CHAT_COMMAND_COLUMNS
    assert indexes == CHAT_COMMAND_INDEXES
    assert TRUNCATION_FILE in applied
    assert [(r["request_id"], r["stdout_tail"], r["anchor_seq"], r["stdout_truncated"]) for r in rows] == [
        ("ccr_history", "out", 3, 0)
    ]
    assert _boot_like_config(db_path) == []  # 재부팅은 아무것도 하지 않는다
def test_a_v02_first_db_carries_its_copies_over_and_boots(tmp_path):
    """10-06~10-08 v0.2 이력. 장부의 137/138 은 136/137 로 옮겨 적힐 뿐 다시 실행되지 않는다.

    SQLite 의 157 은 표를 재생성하며 두 이력에 공통인 컬럼만 옮기므로, 이 이력의 기존 행은
    truncated 표시만 0 으로 돌아간다(출력 tail 은 남는다). PostgreSQL/MySQL 의 157 은
    ADD COLUMN IF NOT EXISTS 라 값도 남는다.
    """
    db_path = _db_from_history(tmp_path, "chat_v02_first", _v02_first_history())
    _insert_command_row(db_path, {"stdout_truncated": 1})

    assert sorted(_boot_like_config(db_path)) == sorted(
        (copy, main) for main, copy in V02_CHAT_COPIES.items()
    )

    columns, indexes, rows, applied = _chat_command_shape(db_path)
    assert columns == CHAT_COMMAND_COLUMNS
    assert indexes == CHAT_COMMAND_INDEXES
    assert {*MAIN_CHAT_FILES, TRUNCATION_FILE} <= applied
    assert [(r["request_id"], r["stdout_tail"], r["anchor_seq"], r["stdout_truncated"]) for r in rows] == [
        ("ccr_history", "out", 3, 0)
    ]
    assert _boot_like_config(db_path) == []
def test_without_the_carryover_a_v02_first_db_really_reruns_136_and_fails(tmp_path):
    """대조군. 위 초록불이 "136 재실행이 원래 무해해서"가 아님을 보인다."""
    from sqloader import SQLiteWrapper, DatabaseMigrator

    db_path = _db_from_history(tmp_path, "chat_v02_control", _v02_first_history())

    # reconcile_renamed_migrations() 를 일부러 호출하지 않는다.
    with pytest.raises(Exception) as caught:
        DatabaseMigrator(SQLiteWrapper(db_name=db_path), str(MIGRATIONS_DIR / "sqlite"), True)

    message = str(caught.value)
    assert MAIN_CHAT_FILES[0] in message, message
    assert "already exists" in message, message
