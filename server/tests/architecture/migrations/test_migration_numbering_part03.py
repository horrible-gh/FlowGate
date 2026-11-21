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

        # 0408 TR0018: 장부에는 최종 이름 하나당 행이 하나뿐이고, 같은 최종 이름으로
        # 수렴하는 옛 이름이 둘인 항목(078b/079b)도 어느 DB 에서나 둘 중 하나만 실제로
        # 적용돼 있다. 아래 UPDATE 는 RENAMES 순서상 첫 번째 옛 이름 하나만 되돌리므로
        # 정확히 그 실제 DB 모양을 만든다 — 그래서 되돌아가는 행 수는 len(RENAMES) 가
        # 아니라 서로 다른 최종 이름의 개수다.
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
    # 뒤따르는 두 파일은 번호도 이름도 그대로여야 한다.
    assert {"087_tr_commit_reapply.sql", "088_tr_conflict_session.sql"} <= names
@pytest.mark.parametrize("dialect", DIALECTS)
def test_086a_keeps_its_place_between_086_and_087(dialect):
    """글자 접미를 쓴 이유가 정렬 순서 보존이다.

    번호를 089 로 밀어 올리면 087/088 뒤로 가버린다. 086a 는 (아직 병합되지 않은)
    병렬 작업의 086 바로 뒤, 이 그룹 자신의 087 앞이라는 원래 자리를 그대로 지킨다.
    """
    order = [p.name for p in _files(dialect)]  # _files 는 이미 이름순 정렬이다
    assert order.index(LEDGER_FINAL_NAME) < order.index("087_tr_commit_reapply.sql")
    assert order.index("087_tr_commit_reapply.sql") < order.index("088_tr_conflict_session.sql")
    # 먼저 온 086 이 병합돼 들어오더라도 그 뒤여야 한다. 아직 없으면 이 단언은 건너뛴다.
    siblings = [n for n in order if FILE_NAME.match(n).group("ordinal") == "086"]
    for sibling in siblings:
        assert sibling < LEDGER_FINAL_NAME
    # 086 과 086a 는 서로 다른 순번이므로 중복으로 보고되지 않는다.
    ordinals = [FILE_NAME.match(n).group("ordinal") for n in order]
    assert ordinals.count("086a") == 1
    assert ordinals.count("086") == len(siblings)
def test_both_ledger_histories_converge_directly_on_the_final_name():
    """중간 이름 086 을 거치게 하지 않는다.

    085 -> 086, 086 -> 086a 로 사슬을 만들면 085 를 든 DB 는 RENAMES 를 한 번 훑는
    동안 두 항목을 순서대로 맞아 우연히 086a 까지 갈 수도, 순서가 바뀌면 디스크에 없는
    086 에 멈출 수도 있다. 두 이력 모두 최종 이름을 직접 가리켜야 한다.
    """
    ledger_entries = {
        (old, new) for old, new in RENAMES if old in LEDGER_APPLIED_HISTORIES
    }
    assert ledger_entries == {
        (old, LEDGER_FINAL_NAME) for old in LEDGER_APPLIED_HISTORIES
    }, f"두 이력이 {LEDGER_FINAL_NAME} 로 직접 수렴하지 않는다: {sorted(ledger_entries)}"

    # 다른 파일의 기존 이력은 건드리지 않았다.
    assert ("086_tr_commit_reapply.sql", "087_tr_commit_reapply.sql") in RENAMES
@pytest.mark.parametrize("history", LEDGER_APPLIED_HISTORIES)
def test_either_ledger_history_alone_is_carried_to_086a(tmp_path, history):
    """두 이력을 각각 따로 넣고 돌린다 — 어느 쪽 DB 도 다른 쪽 이름은 갖고 있지 않다."""
    path = TestFilenameCarryOver._make_db(
        tmp_path, ["001_flowgate_schema.sql", history]
    )

    carried = apply_migration_renames("sqlite3", sqlite_path=path)

    assert carried == 1, f"{history} 이력 하나만 이관돼야 한다"
    assert TestFilenameCarryOver._names(path) == {
        "001_flowgate_schema.sql",
        LEDGER_FINAL_NAME,
    }

    # 두 번째 부팅은 0 건이고 장부도 그대로다.
    before = TestFilenameCarryOver._names(path)
    assert apply_migration_renames("sqlite3", sqlite_path=path) == 0
    assert TestFilenameCarryOver._names(path) == before
@pytest.mark.parametrize("history", LEDGER_APPLIED_HISTORIES)
def test_real_migrator_reboot_after_ledger_carryover_does_not_rerun_the_ledger(
    tmp_path, history
):
    """장부 행 확인이 아니라 진짜 재부팅이다.

    `config.py` `instance_init()` 과 같은 순서로 돈다: 새 설치 -> 장부를 그 이력의 옛
    이름으로 되돌려 이미 적용된 구형 DB 를 재현 -> `apply_migration_renames()` ->
    같은 디스크 디렉터리로 `DatabaseMigrator` 를 새로 세운다. 이관이 새면 여기서
    `Failed to apply migration 086a_tr_commit_ledger.sql: duplicate column name:
    last_cancel_block_reason` 로 실제 부팅과 똑같이 선다(바로 아래 대조군이 그 실패를
    보인다). MySQL 방언은 같은 자리에서 IF NOT EXISTS 없는 ADD COLUMN 세 개를 다시
    실행하므로 위험이 더 크고, 막는 장치는 양쪽 다 이 장부 이관 하나뿐이다.
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
    """대조군. 위 두 케이스의 초록불이 "재적용이 원래 무해해서"가 아님을 보인다.

    ledger SQL 은 표와 인덱스는 IF NOT EXISTS 지만 `group_git_state` 의 ADD COLUMN 세
    개는 어느 방언에도 그런 절이 없다. 그래서 장부 이관을 건너뛰면 sqloader 가 086a 를
    새 파일로 알고 다시 적용하다 첫 ADD COLUMN 에서 선다.
    """
    from sqloader import SQLiteWrapper, DatabaseMigrator

    db_path = _fresh_real_db(tmp_path, "control.db")
    _rewind_ledger_row(db_path, LEDGER_APPLIED_HISTORIES[0])

    # apply_migration_renames() 를 일부러 호출하지 않는다.
    with pytest.raises(Exception) as caught:
        DatabaseMigrator(SQLiteWrapper(db_name=db_path), str(MIGRATIONS_DIR / "sqlite"), True)

    message = str(caught.value)
    assert LEDGER_FINAL_NAME in message, message
    assert "last_cancel_block_reason" in message, message
