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

@pytest.mark.parametrize("dialect", DIALECTS)
def test_the_081_workflow_sequence_provider_collision_sits_at_081a_in_every_dialect(dialect):
    names = {p.name for p in _files(dialect)}
    assert WORKFLOW_PROVIDER_FINAL_NAME in names, f"{dialect}/{WORKFLOW_PROVIDER_FINAL_NAME} 이 없다"
    for stale in WORKFLOW_PROVIDER_APPLIED_HISTORIES:
        assert stale not in names, (
            f"{dialect}/{stale} 이 되살아났다. 이동은 복사가 아니다 — 옛 이름이 디스크에 "
            f"있으면 이미 이관한 DB 가 그 파일을 통째로 다시 적용한다."
        )
    # 081_document_origin_snapshot.sql 과의 충돌을 풀려는 이동이므로, 그 파일과 뒤따르는
    # 082 는 번호도 이름도 그대로여야 한다.
    assert {"081_document_origin_snapshot.sql", "082_document_origin_backfill.sql"} <= names
@pytest.mark.parametrize("dialect", DIALECTS)
def test_081a_keeps_its_place_between_081_and_082(dialect):
    order = [p.name for p in _files(dialect)]  # _files 는 이미 이름순 정렬이다
    assert order.index("081_document_origin_snapshot.sql") < order.index(WORKFLOW_PROVIDER_FINAL_NAME)
    assert order.index(WORKFLOW_PROVIDER_FINAL_NAME) < order.index("082_document_origin_backfill.sql")
    # 081 과 081a 는 서로 다른 순번이므로 중복으로 보고되지 않는다.
    ordinals = [FILE_NAME.match(n).group("ordinal") for n in order]
    assert ordinals.count("081a") == 1
    assert ordinals.count("081") == 1
def test_both_workflow_sequence_provider_histories_converge_directly_on_081a():
    """중간 이름 081 을 거치게 하지 않는다 (086a 사례와 같은 이유)."""
    entries = {
        (old, new) for old, new in RENAMES if old in WORKFLOW_PROVIDER_APPLIED_HISTORIES
    }
    assert entries == {
        (old, WORKFLOW_PROVIDER_FINAL_NAME) for old in WORKFLOW_PROVIDER_APPLIED_HISTORIES
    }, f"두 이력이 {WORKFLOW_PROVIDER_FINAL_NAME} 로 직접 수렴하지 않는다: {sorted(entries)}"
@pytest.mark.parametrize("history", WORKFLOW_PROVIDER_APPLIED_HISTORIES)
def test_either_workflow_sequence_provider_history_alone_is_carried_to_081a(tmp_path, history):
    """두 이력을 각각 따로 넣고 돌린다 — 어느 쪽 DB 도 다른 쪽 이름은 갖고 있지 않다."""
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
    """장부 행 확인이 아니라 진짜 재부팅이다 — 반려 사유 재현.

    `config.py` `instance_init()` 과 같은 순서로 돈다: 새 설치 -> 장부를 그 이력의 옛
    이름으로 되돌려 이미 적용된 구형 DB 를 재현 -> `apply_migration_renames()` -> 같은
    디스크 디렉터리로 `DatabaseMigrator` 를 새로 세운다. 이관이 새면 여기서
    `Failed to apply migration 081a_workflow_sequence_provider.sql: duplicate column name:
    provider_id` 로, 실제 반려 사유("Failed to apply migration
    081_workflow_sequence_provider.sql: duplicate column name: provider_id")와 같은 모양
    으로 선다(바로 아래 대조군이 그 실패를 보인다).
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
    """대조군. 반려 사유("duplicate column name: provider_id")가 실제로 재현되는가.

    081/080 둘 다 `provider_id`/`provider_display_name` 을 IF NOT EXISTS 없이 ADD COLUMN
    하므로, 장부 이관을 건너뛰면 sqloader 가 081a 를 새 파일로 알고 다시 적용하다
    첫 ADD COLUMN 에서 정확히 반려 사유 그대로 선다.
    """
    from sqloader import SQLiteWrapper, DatabaseMigrator

    db_path = _fresh_real_db(tmp_path, "wf_provider_control.db")
    _rewind_workflow_provider_row(db_path, WORKFLOW_PROVIDER_APPLIED_HISTORIES[1])

    # apply_migration_renames() 를 일부러 호출하지 않는다.
    with pytest.raises(Exception) as caught:
        DatabaseMigrator(SQLiteWrapper(db_name=db_path), str(MIGRATIONS_DIR / "sqlite"), True)

    message = str(caught.value)
    assert WORKFLOW_PROVIDER_FINAL_NAME in message, message
    assert "duplicate column name: provider_id" in message, message
def test_the_095_rename_is_in_the_ledger():
    assert (MIGRATION_095_OLD_NAME, MIGRATION_095_FINAL_NAME) in RENAMES
