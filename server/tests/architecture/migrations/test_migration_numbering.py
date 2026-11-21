"""Global migration-file guards (flowgate.default.0394 T0004, recommendation 2).

NR0003 section 5.3 found ten groups of duplicate migration numbers but only one
failing guard: test_chat_settings_0362 checked only 076. The preceding seven
groups had no tests and remained green despite their defects.

The rule is global, so inspect the complete file sets of all three dialects,
regardless of the dialect or filename of a new migration. Enforce five rules:

1. Filenames follow NNN[a-z]_name.sql. The loader sorts filenames to determine
   execution order (sqloader/migrator.py get_migration_files).
2. Ordinals are unique, catching parallel groups claiming the same number.
3. All three dialects contain the same files; a missing dialect silently omits
   a migration on that database engine.
4. No index redefinition. Extend test_document_index_coverage_0291's check of
   the five indexes from 070 to all indexes, parsing CREATE INDEX statements
   after removing comments instead of using substring membership.
5. Renamed files and migration_renames.RENAMES agree with the actual disk state.

TestFilenameCarryOver verifies the ledger behavior against temporary databases.
sqloader tracks applied migrations by filename, so a rename would otherwise
make an already-migrated database execute a file again. Ledger carry-over is
the mechanism that prevents this.
"""

from __future__ import annotations

import re
import sqlite3
import sys
from pathlib import Path

import pytest

from migration_contracts import shared_migration_files

_SERVER_DIR = Path(__file__).resolve().parents[3]
if str(_SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(_SERVER_DIR))

from modules.flow_gate.db.migration_renames import (  # noqa: E402
    RENAMES,
    apply_migration_renames,
)

MIGRATIONS_DIR = _SERVER_DIR / "sql" / "migrations"
DIALECTS = ("sqlite", "postgres", "mysql")
REQUIRED_COLLISION_RENAMES = {
    ("078_continuation_auto_approve.sql", "078a_continuation_auto_approve.sql"),
    ("078_seed_work_plan_doctype.sql", "078b_seed_work_plan_doctype.sql"),
    ("079_ai_invoke_step_timeout.sql", "079a_ai_invoke_step_timeout.sql"),
    ("079_workflow_sequence_note_source.sql", "079b_workflow_sequence_note_source.sql"),
    ("080_ai_invoke_prompt_audit.sql", "080a_ai_invoke_prompt_audit.sql"),
    # The provider file left 080 entirely; §9 below owns its three histories and the
    # 081a they all converge on. Naming 080b here would contradict it.
    ("080_workflow_sequence_provider.sql", "081a_workflow_sequence_provider.sql"),
}

# NNN, optionally followed by one lowercase letter. The letter is how a file that
# arrived second on the same number keeps its position in sort order without
# colliding: "076_x.sql" < "076a_y.sql" < "076b_z.sql" < "077_w.sql".
FILE_NAME = re.compile(r"^(?P<ordinal>\d{3}[a-z]?)_[A-Za-z0-9_]+\.sql$")

_CREATE_INDEX = re.compile(
    r"CREATE\s+(?:UNIQUE\s+)?INDEX\s+(?:IF\s+NOT\s+EXISTS\s+)?[`\"]?(\w+)[`\"]?\s+ON\s+[`\"]?(\w+)[`\"]?",
    re.IGNORECASE,
)
_RENAMED_AWAY = re.compile(r"ALTER\s+TABLE\s+[`\"]?(\w+)[`\"]?\s+RENAME\s+TO", re.IGNORECASE)
_DROPPED = re.compile(r"DROP\s+TABLE\s+(?:IF\s+EXISTS\s+)?[`\"]?(\w+)[`\"]?", re.IGNORECASE)


def _files(dialect: str) -> list[Path]:
    return sorted((MIGRATIONS_DIR / dialect).glob("*.sql"))


def _without_comments(sql: str) -> str:
    """Drop -- and /* */ comments.

    This is as error-prone as duplicate numbering. The 0291 guard treated any
    occurrence of an index name as a redefinition, so a comment referencing an
    index from 070 caused a failure. Inspect executable SQL only.
    """
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    return "\n".join(re.sub(r"--.*$", "", line) for line in sql.splitlines())


# 1-2. Filename format and unique ordinals.

@pytest.mark.parametrize("dialect", DIALECTS)
def test_every_file_name_carries_a_parsable_ordinal(dialect):
    bad = [p.name for p in _files(dialect) if not FILE_NAME.match(p.name)]
    assert bad == [], (
        f"{dialect}: 이름이 NNN[a-z]_이름.sql 모양이 아니다: {bad}. "
        f"로더는 이름 정렬로 적용 순서를 정하므로 순번을 읽을 수 없는 이름은 "
        f"그 자체가 순서 사고다."
    )


@pytest.mark.parametrize("dialect", DIALECTS)
def test_no_two_migrations_share_an_ordinal(dialect):
    by_ordinal: dict[str, list[str]] = {}
    for path in _files(dialect):
        match = FILE_NAME.match(path.name)
        assert match, path.name
        by_ordinal.setdefault(match.group("ordinal"), []).append(path.name)

    shared = {k: v for k, v in by_ordinal.items() if len(v) > 1}
    assert shared == {}, (
        f"{dialect}: 한 순번에 파일이 둘 이상이다: {shared}. 병렬 그룹이 같은 번호를 "
        f"집어 갔다는 뜻이다 — 뒤에 온 파일에 알파벳을 붙여(예: 076 -> 076a) 순서를 "
        f"명시하라. 앞 파일과의 상대 순서는 그대로 유지된다."
    )


# 3. All three dialects contain the same file set.

def test_the_three_dialects_hold_the_same_file_set():
    sets = shared_migration_files(MIGRATIONS_DIR)
    reference = sets["sqlite"]
    for dialect in ("postgres", "mysql"):
        missing = sorted(reference - sets[dialect])
        extra = sorted(sets[dialect] - reference)
        assert missing == [], f"{dialect} 에 없는 마이그레이션: {missing}"
        assert extra == [], f"{dialect} 에만 있는 마이그레이션: {extra}"


# 4. No index redefinition: extend the 0291 check beyond migration 070.

@pytest.mark.parametrize("dialect", DIALECTS)
def test_no_migration_recreates_an_index_that_still_exists(dialect):
    """A later recreation of a live index would replace its effective definition.

    Table rebuilds are exempt: SQLite CHECK changes require rebuilding the
    table (042a/062a/064 tokens), dropping its indexes as well. Recreating them
    in that file is legitimate. Likewise, 027 rebuilt documents and dropped
    the indexes from 015, which 070 restored. Forget indexes of removed tables.
    """
    live: dict[str, tuple[str, str]] = {}   # index name -> (file, table)
    clashes = []
    for path in _files(dialect):
        body = _without_comments(path.read_text(encoding="utf-8"))
        # DROP INDEX also ends an index's lifetime (including MySQL's
        # ALTER TABLE ... DROP INDEX form), without dropping its table.
        for name in re.findall(r'DROP\s+INDEX\s+(?:IF\s+EXISTS\s+)?[`"]?(\w+)', body, re.IGNORECASE):
            live.pop(name, None)
        gone = set(_RENAMED_AWAY.findall(body)) | set(_DROPPED.findall(body))
        for name, (_file, table) in list(live.items()):
            if table in gone:
                del live[name]
        for name, table in _CREATE_INDEX.findall(body):
            if name in live:
                clashes.append(f"{name} ({live[name][0]} -> {path.name})")
            else:
                live[name] = (path.name, table)

    assert clashes == [], (
        f"{dialect}: 앞 마이그레이션이 만든 인덱스를 뒤 파일이 다시 정의한다: {clashes}. "
        f"컬럼 구성이 달라도 아무 데도 신고되지 않고, DB 에 남는 것은 뒤엣것이다."
    )


# 5. Renamed files and ledger entries agree with the disk.

def test_the_rename_ledger_matches_what_is_on_disk():
    for old, new in RENAMES:
        for dialect in DIALECTS:
            directory = MIGRATIONS_DIR / dialect
            assert not (directory / old).exists(), (
                f"{dialect}/{old} 이 되살아났다. 장부는 이 이름이 사라진 것으로 적혀 "
                f"있어서, 다시 있으면 이미 이관한 DB 가 이 파일을 통째로 다시 적용한다."
            )
            assert (directory / new).is_file(), f"{dialect}/{new} 이 없다"


def test_the_latest_collision_renames_are_in_the_ledger():
    assert REQUIRED_COLLISION_RENAMES <= set(RENAMES), (
        "078/079 번호 충돌을 고치면서 적용 장부 이관을 빠뜨리면 이미 적용된 DB가 "
        "079b를 새 마이그레이션으로 오인해 note 열을 다시 추가한다."
    )


def test_the_rename_ledger_maps_old_names_unambiguously():
    """Each old name maps to exactly one new name, but not conversely.

    flowgate.default.0408 TR0018: two groups independently resolved the 078/079
    collisions at different times, yielding two historical names for one final
    name. Both 078_seed_work_plan_doctype.sql and 078a_seed_work_plan_doctype.sql
    converge on 078b_seed_work_plan_doctype.sql. A database applied only one
    history, so repeated new names are safe: apply_migration_renames removes
    the old row when both exist. Duplicate old names are ambiguous, and overlap
    between old/new names makes a single pass order-dependent. Guard those two
    unsafe cases only.
    """
    olds = [old for old, _ in RENAMES]
    news = [new for _, new in RENAMES]
    assert len(set(olds)) == len(olds), "장부에 같은 옛 이름이 두 번 있다"
    assert set(olds).isdisjoint(news), "옛 이름과 새 이름이 겹친다"


# 6. Apply all files in order against SQLite.

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


# 7. Verify actual ledger carry-over.

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


# 8. Both applied histories of the 0332 ledger converge on 086a (T0021).
#
# This group created the ledger migration as 085, moved it to 086, then to
# 086a when parallel work claimed 086 first. Thus the same file has two
# applied histories: databases before the first move record 085; later ones
# record 086. None records both. The bulk check in section 7 loads all
# RENAMES together and actually carries over only the first convergent row.
# This section tests each history independently.
#
# Ledger rows alone are insufficient. A missed carry-over makes sqloader
# rerun 086a; ledger SQL adds columns to group_git_state in every dialect,
# and none is idempotent (MySQL is below 8.0.29 without IF NOT EXISTS;
# SQLite has no such syntax). Both tests construct a real DatabaseMigrator
# through the boot path, and the control proves failure without carry-over.

LEDGER_FINAL_NAME = "086d_tr_commit_ledger.sql"
LEDGER_APPLIED_HISTORIES = ("085_tr_commit_ledger.sql", "086_tr_commit_ledger.sql")


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


def _fresh_real_db(tmp_path, name: str) -> str:
    """Build a database using every real sql/migrations/sqlite file in order."""
    from sqloader import SQLiteWrapper, DatabaseMigrator

    db_path = str(tmp_path / name)
    DatabaseMigrator(SQLiteWrapper(db_name=db_path), str(MIGRATIONS_DIR / "sqlite"), True)
    return db_path


def _rewind_ledger_row(db_path: str, history: str) -> None:
    """Rewind the 086a row to its old name to reproduce that database history."""
    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.execute(
            "UPDATE migrations SET filename = ? WHERE filename = ?",
            (history, LEDGER_FINAL_NAME),
        )
        assert cursor.rowcount == 1, (
            f"{LEDGER_FINAL_NAME} 행이 장부에 없다 — 파일 이름이 바뀌었거나 마이그레이션이 "
            f"적용되지 않았다."
        )
        conn.commit()
    finally:
        conn.close()


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


# 9. Provider histories converge on 081a (0452, duplicate column name: provider_id).
#
# T0007 moved 080_workflow_sequence_provider.sql to 081, but parallel group
# flowgate.default.0410 had already claimed 081_document_origin_snapshot.sql.
# This caused a second collision, like 086a above but one ordinal earlier.
# test_no_two_migrations_share_an_ordinal missed it during merge; the real
# DatabaseMigrator reboot exposed duplicate column name: provider_id.
# Merely renaming to 081a without carry-over makes databases that applied
# 080 or 081 treat the file as new and fail when applying it a second time.

WORKFLOW_PROVIDER_FINAL_NAME = "081a_workflow_sequence_provider.sql"
# Three histories exist: 080 before T0007, 081 afterward (recorded in the STG
# PostgreSQL ledger), and a third spelling, 080b, read directly from the
# migrations table in the dev deployment (FlowGate-dev/server/storage/flowgate.db).
# Another branch resolved the same 080 collision with a letter suffix instead.
# This third name caused the second rejection: 081a duplicate provider_id.
WORKFLOW_PROVIDER_APPLIED_HISTORIES = (
    "080_workflow_sequence_provider.sql",
    "080b_workflow_sequence_provider.sql",
    "081_workflow_sequence_provider.sql",
)


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


def _rewind_workflow_provider_row(db_path: str, history: str) -> None:
    """Rewind the 081a row to its old name to reproduce that database history."""
    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.execute(
            "UPDATE migrations SET filename = ? WHERE filename = ?",
            (history, WORKFLOW_PROVIDER_FINAL_NAME),
        )
        assert cursor.rowcount == 1, (
            f"{WORKFLOW_PROVIDER_FINAL_NAME} 행이 장부에 없다 — 파일 이름이 바뀌었거나 "
            f"마이그레이션이 적용되지 않았다."
        )
        conn.commit()
    finally:
        conn.close()


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


# 10. Carry over the 095 slug rename (T0020, duplicate column name: operator_api_base).
#
# 095_ai_invoke_run_transport_diagnostics.sql began as
# 095_ai_invoke_api_diagnostics.sql; only its slug changed, not its ordinal.
# Neither reconcile_renamed_migrations pass handles this: pass 1 requires
# matching ordinal/slug with a different letter suffix; pass 2 requires a
# matching slug with a different ordinal. Only static RENAMES handles this
# case, confirmed from migrations in the dev preview DB, FlowGate/storage/flowgate.db.

MIGRATION_095_OLD_NAME = "095_ai_invoke_api_diagnostics.sql"
MIGRATION_095_FINAL_NAME = "095_ai_invoke_run_transport_diagnostics.sql"


def test_the_095_rename_is_in_the_ledger():
    assert (MIGRATION_095_OLD_NAME, MIGRATION_095_FINAL_NAME) in RENAMES


def test_real_migrator_reboot_after_095_carryover_does_not_rerun_it(tmp_path):
    """Carry-over removes the rejection: duplicate column name: operator_api_base."""
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
    """Control: reproduce the original rejection."""
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

    # Deliberately omit apply_migration_renames().
    with pytest.raises(Exception) as caught:
        DatabaseMigrator(SQLiteWrapper(db_name=db_path), str(MIGRATIONS_DIR / "sqlite"), True)

    message = str(caught.value)
    assert MIGRATION_095_FINAL_NAME in message, message
    assert "duplicate column name: operator_api_base" in message, message


# 11. Remove v0.2 copies of 136/137 and boot all three histories (flowgate.v02.0081).
#
# v0.2 reimplemented main 136_chat_command_execution / 137_chat_activity_anchor
# as 137/138. Merging main on 10-08 duplicated tables/indexes/columns in all
# three dialects, stopping STG boot with idx_chat_cmd_run already exists.
# The copies were removed; 157 adds their stdout/stderr_truncated columns.
# Reboot all three histories in config.py run_migrations() order: construct
# migrator, reconcile_renamed_migrations, then apply_migrations.
#   Empty DB: apply everything through 157.
#   Main first: install v0.2 over main 136/137, matching STG history.
#   v0.2 first: 10-06 to 10-08 databases applied copies 137/138; the second
#   migration_rename_repair pass carries those ledger rows over to 136/137.

MAIN_CHAT_FILES = ("136_chat_command_execution.sql", "137_chat_activity_anchor.sql")
V02_CHAT_COPIES = {
    "136_chat_command_execution.sql": "137_chat_command_execution.sql",
    "137_chat_activity_anchor.sql": "138_chat_activity_anchor.sql",
}
TRUNCATION_FILE = "157_chat_command_output_truncation.sql"
CHAT_COMMAND_COLUMNS = {
    "request_id", "ai_run_id", "doc_id", "project_id", "group_id", "token_id", "issued_to",
    "provider_name", "program", "args_json", "cwd_relative", "timeout_seconds", "category",
    "policy", "status", "decision_source", "decided_by", "decided_at", "started_at",
    "finished_at", "duration_ms", "exit_code", "timed_out", "stdout_tail", "stderr_tail",
    "stdout_truncated", "stderr_truncated", "error_code", "created_at", "updated_at",
    "run_start_seq", "anchor_seq", "anchor_position", "anchor_state",
}
CHAT_COMMAND_INDEXES = {"idx_chat_cmd_run", "idx_chat_cmd_doc", "idx_chat_cmd_status", "idx_chat_cmd_anchor"}


@pytest.mark.parametrize("dialect", DIALECTS)
def test_the_v02_copies_of_main_chat_migrations_are_gone(dialect):
    names = {p.name for p in _files(dialect)}
    assert set(MAIN_CHAT_FILES) | {TRUNCATION_FILE} <= names
    revived = sorted(set(V02_CHAT_COPIES.values()) & names)
    assert revived == [], (
        f"{dialect}: v0.2 사본 {revived} 이 되살아났다. main 의 136/137 과 같은 표·인덱스·컬럼을 "
        f"다시 만들어 부팅이 idx_chat_cmd_run already exists 로 선다."
    )


def _boot_like_config(db_path: str) -> list[tuple[str, str]]:
    from sqloader import SQLiteWrapper, DatabaseMigrator
    from migration_rename_repair import reconcile_renamed_migrations

    db = SQLiteWrapper(db_name=db_path)
    migrator = DatabaseMigrator(db, str(MIGRATIONS_DIR / "sqlite"), False)
    pairs = reconcile_renamed_migrations(db, str(MIGRATIONS_DIR / "sqlite"))
    migrator.apply_migrations()
    return pairs


def _db_from_history(tmp_path, name: str, files: dict[str, str]) -> str:
    """Use the real migrator on a temporary file set to build that database history."""
    from sqloader import SQLiteWrapper, DatabaseMigrator

    history_dir = tmp_path / f"{name}_migrations"
    history_dir.mkdir()
    for filename, body in files.items():
        (history_dir / filename).write_text(body, encoding="utf-8")
    db_path = str(tmp_path / f"{name}.db")
    DatabaseMigrator(SQLiteWrapper(db_name=db_path), str(history_dir), True)
    return db_path


def _main_first_history() -> dict[str, str]:
    """Main through 136/137: the exact point where v0.2 failed to boot on STG."""
    return {
        p.name: p.read_text(encoding="utf-8")
        for p in _files("sqlite") if p.name <= MAIN_CHAT_FILES[-1]
    }


def _v02_first_history() -> dict[str, str]:
    """History with v0.2 copies 137/138 instead of main 136/137, plus two truncated columns."""
    files = _main_first_history()
    command = files.pop(MAIN_CHAT_FILES[0])
    marker = "    stderr_tail      TEXT NULL,\n"
    assert marker in command, "136 의 표 정의가 바뀌었다 — 사본 재현을 다시 맞춰야 한다"
    files[V02_CHAT_COPIES[MAIN_CHAT_FILES[0]]] = command.replace(
        marker,
        marker
        + "    stdout_truncated INTEGER NOT NULL DEFAULT 0 CHECK (stdout_truncated IN (0, 1)),\n"
        + "    stderr_truncated INTEGER NOT NULL DEFAULT 0 CHECK (stderr_truncated IN (0, 1)),\n",
    )
    files[V02_CHAT_COPIES[MAIN_CHAT_FILES[1]]] = files.pop(MAIN_CHAT_FILES[1])
    return files


def _insert_command_row(db_path: str, extra: dict[str, int] | None = None) -> None:
    row = {
        "request_id": "ccr_history", "ai_run_id": "aiv_history", "doc_id": "p.v.0001.0001-CH",
        "project_id": "p", "group_id": "p.v.0001", "program": "git", "args_json": "[]",
        "cwd_relative": ".", "timeout_seconds": 60, "category": "git_read",
        "policy": "always_approve", "status": "succeeded", "stdout_tail": "out",
        "stderr_tail": "err", "created_at": "2026-10-21T00:00:00+09:00",
        "updated_at": "2026-10-21T00:00:00+09:00", "anchor_seq": 3,
        **(extra or {}),
    }
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            f"INSERT INTO chat_command_requests ({', '.join(row)}) "
            f"VALUES ({', '.join('?' for _ in row)})",
            tuple(row.values()),
        )
        conn.commit()
    finally:
        conn.close()


def _chat_command_shape(db_path: str):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        columns = {r["name"] for r in conn.execute("PRAGMA table_info(chat_command_requests)")}
        indexes = {
            r["name"] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='chat_command_requests' "
                "AND name NOT LIKE 'sqlite_autoindex_%'"
            )
        }
        rows = [dict(r) for r in conn.execute("SELECT * FROM chat_command_requests")]
        applied = {r["filename"] for r in conn.execute("SELECT filename FROM migrations")}
        return columns, indexes, rows, applied
    finally:
        conn.close()


def test_a_fresh_db_gets_the_whole_chat_command_shape_and_reboots(tmp_path):
    db_path = _fresh_real_db(tmp_path, "chat_fresh.db")
    assert _boot_like_config(db_path) == []
    columns, indexes, _rows, applied = _chat_command_shape(db_path)
    assert columns == CHAT_COMMAND_COLUMNS
    assert indexes == CHAT_COMMAND_INDEXES
    assert {*MAIN_CHAT_FILES, TRUNCATION_FILE} <= applied


def test_a_main_first_db_boots_v02_and_keeps_its_rows(tmp_path):
    """STG history where v0.2 boot failed with idx_chat_cmd_run already exists."""
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
    assert _boot_like_config(db_path) == []  # Reboot makes no changes.


def test_a_v02_first_db_carries_its_copies_over_and_boots(tmp_path):
    """v0.2 history from 10-06 to 10-08: carry 137/138 to 136/137 without rerunning.

    SQLite 157 rebuilds the table and copies only columns shared by both
    histories, resetting truncated flags to zero while preserving output tails.
    PostgreSQL/MySQL 157 uses ADD COLUMN IF NOT EXISTS and preserves the flags.
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
    """Control: prove the pass does not depend on rerunning 136 being harmless."""
    from sqloader import SQLiteWrapper, DatabaseMigrator

    db_path = _db_from_history(tmp_path, "chat_v02_control", _v02_first_history())

    # Deliberately omit reconcile_renamed_migrations().
    with pytest.raises(Exception) as caught:
        DatabaseMigrator(SQLiteWrapper(db_name=db_path), str(MIGRATIONS_DIR / "sqlite"), True)

    message = str(caught.value)
    assert MAIN_CHAT_FILES[0] in message, message
    assert "already exists" in message, message
