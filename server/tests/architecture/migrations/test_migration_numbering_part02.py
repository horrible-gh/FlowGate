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

def test_every_sqlite_migration_applies_in_name_order():
    """Execute every migration in filename order.

    Renumbering can change execution order. For example, tokens is rebuilt
    four times in 042a/062a/064/075; moving an intervening ADD COLUMN can make
    a rebuild SELECT a missing column. Do not swallow exceptions here.
    """
    conn = sqlite3.connect(":memory:")
    try:
        for path in _files("sqlite"):
            try:
                conn.executescript(path.read_text(encoding="utf-8"))
            except sqlite3.Error as exc:
                pytest.fail(f"{path.name} 적용 실패: {exc}")
    finally:
        conn.close()
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

    def test_an_already_migrated_db_gets_every_name_carried_over(self, tmp_path):
        olds = [old for old, _ in RENAMES]
        path = self._make_db(tmp_path, ["001_flowgate_schema.sql", *olds])

        carried = apply_migration_renames("sqlite3", sqlite_path=path)

        assert carried == len(RENAMES)
        names = self._names(path)
        assert names == {"001_flowgate_schema.sql", *[new for _, new in RENAMES]}

    def test_running_it_again_changes_nothing(self, tmp_path):
        path = self._make_db(tmp_path, [old for old, _ in RENAMES])
        apply_migration_renames("sqlite3", sqlite_path=path)
        before = self._names(path)

        assert apply_migration_renames("sqlite3", sqlite_path=path) == 0
        assert self._names(path) == before

    def test_a_db_without_the_tracking_table_is_left_alone(self, tmp_path):
        # Fresh install: no table means nothing to migrate; do not create it here.
        # sqloader will create the table itself immediately afterward.
        path = self._make_db(tmp_path, None)

        assert apply_migration_renames("sqlite3", sqlite_path=path) == 0

        conn = sqlite3.connect(path)
        try:
            tables = {
                r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
        finally:
            conn.close()
        assert "migrations" not in tables

    def test_a_missing_db_file_is_not_created(self, tmp_path):
        path = str(tmp_path / "never_existed.db")

        assert apply_migration_renames("sqlite3", sqlite_path=path) == 0
        assert not Path(path).exists()

    def test_a_stale_old_row_beside_the_new_one_is_dropped(self, tmp_path):
        # An operator applied the new file manually, or an earlier carry-over stopped.
        # Restore exactly one ledger row per file.
        old, new = RENAMES[0]
        path = self._make_db(tmp_path, [old, new])

        assert apply_migration_renames("sqlite3", sqlite_path=path) == 1
        assert self._names(path) == {new}

    def test_a_db_that_never_saw_the_old_names_is_untouched(self, tmp_path):
        # A database initially migrated under the new names; convergent entries may
        # duplicate news, so use a set to create exactly one row per file.
        news = sorted({new for _, new in RENAMES})
        path = self._make_db(tmp_path, news)

        assert apply_migration_renames("sqlite3", sqlite_path=path) == 0
        assert self._names(path) == set(news)
