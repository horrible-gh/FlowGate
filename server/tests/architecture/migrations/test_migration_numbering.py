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



# 7. Verify actual ledger carry-over.



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
