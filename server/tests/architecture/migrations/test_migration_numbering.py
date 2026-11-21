"""Global guards for the migration file set (flowgate.default.0394 T0004, 권고 2).

NR0003 §5.3 의 지적이 이 파일의 존재 이유다. 마이그레이션 번호 중복은 10묶음이었는데
빨간불은 1건뿐이었다 — 076 을 검사하는 가드가 `test_chat_settings_0362` 안에
**076 만 보도록** 쓰여 있었기 때문이다. 앞선 7묶음은 검사하는 테스트가 아예 없어서
결함이 있는 채로 계속 초록이었다.

규칙은 전역인데 검사가 국소였다. 그래서 여기서는 **파일 하나가 아니라 세 방언의 전체
파일 집합**을 본다. 새 마이그레이션이 어느 방언에 어떤 이름으로 들어오든 이 스위트를
지나가야 한다.

고정하는 것은 다섯 가지다.

1. 파일 이름의 모양 — `NNN[a-z]_이름.sql`. 로더가 이름을 정렬해 적용 순서를 정하므로
   (sqloader/migrator.py `get_migration_files`), 이름이 곧 순서다.
2. 순번 유일성 — 두 파일이 같은 순번을 가질 수 없다. 병렬 그룹이 같은 번호를 집어
   가면 여기서 걸린다.
3. 방언 3벌의 파일 집합 일치 — 한 방언에만 들어온 마이그레이션은 다른 엔진에서
   조용히 빠진다.
4. 인덱스 재정의 금지 — `test_document_index_coverage_0291` 이 070 이 만든 5개에
   대해서만 하던 검사를, 전 인덱스로 넓힌다. 문자열 포함이 아니라 CREATE INDEX 문을
   읽고, 주석은 지운 뒤 본다.
5. 번호 정리로 이름이 바뀐 파일과 그 장부(`migration_renames.RENAMES`)가 실제 디스크
   상태와 일치하는가.

`TestFilenameCarryOver` 는 그 장부가 실제로 동작하는지를 임시 DB 로 확인한다.
sqloader 는 **파일 이름**으로 적용 여부를 판단하므로, 이름을 바꾸면 이미 적용한 DB 가
그 파일들을 새 마이그레이션으로 착각한다. 장부 이관이 그것을 막는 유일한 장치다.
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

    번호 중복만큼이나 자주 틀리는 자리다. 0291 의 가드는 파일 텍스트에 인덱스 이름이
    들어 있기만 하면 재정의로 보았고, 그래서 "070 의 그 인덱스처럼"이라고 적은 **주석**
    한 줄이 빨간불이 됐다. 검사는 실행되는 SQL 만 봐야 한다.
    """
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    return "\n".join(re.sub(r"--.*$", "", line) for line in sql.splitlines())


# ── 1~2. 이름 모양과 순번 유일성 ──────────────────────────────────────────────

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


# ── 3. 방언 3벌이 같은 집합인가 ───────────────────────────────────────────────

def test_the_three_dialects_hold_the_same_file_set():
    sets = shared_migration_files(MIGRATIONS_DIR)
    reference = sets["sqlite"]
    for dialect in ("postgres", "mysql"):
        missing = sorted(reference - sets[dialect])
        extra = sorted(sets[dialect] - reference)
        assert missing == [], f"{dialect} 에 없는 마이그레이션: {missing}"
        assert extra == [], f"{dialect} 에만 있는 마이그레이션: {extra}"


# ── 4. 인덱스 재정의 금지 (0291 의 070 전용 검사를 전 인덱스로) ──────────────

@pytest.mark.parametrize("dialect", DIALECTS)
def test_no_migration_recreates_an_index_that_still_exists(dialect):
    """살아 있는 인덱스를 뒤 파일이 다시 만들면 실효 정의가 뒤엣것이 된다.

    표를 재생성하는 파일은 예외다 — SQLite 에서 CHECK 를 바꾸려면 표를 다시 만들어야
    하고(042a/062a/064 tokens), 그때 인덱스도 함께 사라지므로 같은 파일 안에서 다시
    만드는 것이 정상이다. 027 이 documents 를 재생성하면서 015 의 인덱스를 날렸고
    070 이 그것을 복구한 것도 같은 경우다. 그래서 "표가 사라졌으면 잊는다"로 센다.
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


# ── 5. 이름이 바뀐 파일과 장부가 디스크와 맞는가 ──────────────────────────────

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
    """`old` 하나는 정확히 하나의 `new` 로만 간다 — 그 반대는 아니다.

    flowgate.default.0408 TR0018: 078/079 는 두 그룹이 서로 다른 시점에 같은 순번
    충돌을 독립적으로 풀어, 같은 최종 이름으로 수렴하는 옛 이름이 둘 생겼다
    (예: `078_seed_work_plan_doctype.sql` 과 `078a_seed_work_plan_doctype.sql`
    모두 `078b_seed_work_plan_doctype.sql` 로 수렴). 어느 DB 도 두 이력을 동시에
    갖지는 않으므로(둘 중 하나만 실제로 적용됐다) `new` 쪽 중복은 안전하다 —
    `apply_migration_renames` 의 "둘 다 있으면 옛 것을 지운다" 분기가 정확히 이
    경우를 처리한다. 위험한 것은 `old` 쪽 중복(어느 new 로 가는지 모호해짐)과
    `old`/`new` 가 겹치는 경우(한 패스 안에서 순서에 따라 결과가 갈릴 수 있음)
    뿐이라 그 둘만 고정한다.
    """
    olds = [old for old, _ in RENAMES]
    news = [new for _, new in RENAMES]
    assert len(set(olds)) == len(olds), "장부에 같은 옛 이름이 두 번 있다"
    assert set(olds).isdisjoint(news), "옛 이름과 새 이름이 겹친다"


# ── 6. 순서대로 다 적용되는가 (sqlite 실측) ───────────────────────────────────



# ── 7. 장부 이관이 실제로 동작하는가 ──────────────────────────────────────────



# ── 8. 0332 ledger 의 두 적용 이력이 086a 로 수렴하는가 (T0021) ───────────────
#
# 이 그룹의 ledger 마이그레이션은 085 로 태어나 086 으로 한 번 옮겨졌고, 병렬 작업이
# 086 을 먼저 가져가면서 086a 로 한 번 더 옮겨졌다. 그래서 **같은 파일에 이미 적용된
# 이력이 둘**이다 — 첫 이동 전에 이 브랜치를 돌린 DB 는 085 를, 첫 이동 뒤에 돌린 DB 는
# 086 을 장부에 들고 있고, 어느 DB 도 둘을 동시에 갖지 않는다. 위의 일괄 검사(7절)는
# RENAMES 전체를 한꺼번에 넣고 돌리므로 수렴 항목 중 **첫 줄 하나만** 실제로 이관되는
# 모양을 만든다. 두 이력을 각각 독립적으로 고정하는 것은 여기뿐이다.
#
# 그리고 장부 행만 보는 것으로는 부족하다. 이관이 새면 sqloader 가 086a 를 새 파일로
# 알고 다시 적용하는데, ledger SQL 은 세 방언 모두 `group_git_state` 에 ADD COLUMN 을
# 하고 그 어느 것도 멱등이 아니다(MySQL 은 8.0.29 미만이라 IF NOT EXISTS 자체가 없고,
# SQLite 는 문법이 아예 없다). 그래서 아래 두 케이스는 진짜 `DatabaseMigrator` 를 다시
# 세워 부팅 경로까지 통과시키고, 마지막 대조군이 "이관을 빼면 정말로 선다"를 보인다.

LEDGER_FINAL_NAME = "086d_tr_commit_ledger.sql"
LEDGER_APPLIED_HISTORIES = ("085_tr_commit_ledger.sql", "086_tr_commit_ledger.sql")










def _fresh_real_db(tmp_path, name: str) -> str:
    """실제 sql/migrations/sqlite 를 처음부터 끝까지 적용한 DB 를 만든다."""
    from sqloader import SQLiteWrapper, DatabaseMigrator

    db_path = str(tmp_path / name)
    DatabaseMigrator(SQLiteWrapper(db_name=db_path), str(MIGRATIONS_DIR / "sqlite"), True)
    return db_path


def _rewind_ledger_row(db_path: str, history: str) -> None:
    """086a 행 하나를 옛 이름으로 되돌려, 그 이력을 가진 실제 DB 모양을 만든다."""
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






# ── 9. 081 순번의 두 이력이 081a 로 수렴하는가 (0452, "duplicate column name: provider_id") ──
#
# 080_workflow_sequence_provider.sql 은 T0007 이 081 로 옮겼는데, 그 081 을
# flowgate.default.0410 이 병렬 브랜치에서 081_document_origin_snapshot.sql 로 독자적으로
# 먼저 차지해 두 번째 충돌이 생겼다(위 086a 사례와 같은 모양, 한 순번 앞). 이번에는
# `test_no_two_migrations_share_an_ordinal` 이 병합 시점에 잡지 못한 채로 있다가, 실제
# `DatabaseMigrator` 재부팅에서 `duplicate column name: provider_id` 로 드러났다 — 이관 없이
# 081a 로만 옮기면 080 이나 081 로 이미 적용해 둔 DB 가 재부팅 때 이 파일을 새 마이그레이션
# 으로 착각해 두 번째로 적용하다 죽는다.

WORKFLOW_PROVIDER_FINAL_NAME = "081a_workflow_sequence_provider.sql"
# 이력이 셋이다. 080 은 T0007 이전, 081 은 T0007 이후(stg PostgreSQL 장부가 이 이름을
# 들고 있다), 080b 는 그 둘 어느 쪽도 아닌 **세 번째 철자**로, 실제 dev 배포
# (FlowGate-dev/server/storage/flowgate.db)의 `migrations` 표에서 직접 읽었다 — 같은 080
# 충돌을 T0007 과 다른 방식(글자 접미)으로 푼 또 다른 브랜치가 남긴 것이다. 이 세 번째
# 이름이 이 문서의 두 번째 반려("081a_… : duplicate column name: provider_id")의 원인이다.
WORKFLOW_PROVIDER_APPLIED_HISTORIES = (
    "080_workflow_sequence_provider.sql",
    "080b_workflow_sequence_provider.sql",
    "081_workflow_sequence_provider.sql",
)










def _rewind_workflow_provider_row(db_path: str, history: str) -> None:
    """081a 행 하나를 옛 이름으로 되돌려, 그 이력을 가진 실제 DB 모양을 만든다."""
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






# ── 10. 095 순번의 슬러그 개명이 이관되는가 (T0020, "duplicate column name: operator_api_base") ──
#
# 095_ai_invoke_run_transport_diagnostics.sql 은 095_ai_invoke_api_diagnostics.sql 이라는
# 이름으로 태어나 순번은 그대로 두고 슬러그만 바뀌었다. `reconcile_renamed_migrations` 의 두
# 패스 모두 이 모양을 못 본다 — 1패스는 순번+슬러그가 같고 글자 접미만 다른 경우, 2패스는
# 슬러그가 같고 순번만 다른 경우만 잡는다. 순번은 같고 슬러그만 다른 순수 개명은 정적
# RENAMES 장부로만 이관할 수 있다. 실제 dev 프리뷰 DB(FlowGate/storage/flowgate.db)의
# `migrations` 표에서 옛 이름을 직접 읽어 확인했다.

MIGRATION_095_OLD_NAME = "095_ai_invoke_api_diagnostics.sql"
MIGRATION_095_FINAL_NAME = "095_ai_invoke_run_transport_diagnostics.sql"








# ── 11. v0.2 의 136/137 사본이 남지 않고, 세 DB 이력이 모두 부팅되는가 (flowgate.v02.0081) ──
#
# v0.2 는 main 의 136_chat_command_execution / 137_chat_activity_anchor 를 137/138 로 다시
# 구현해 들고 있었다. 10-08 에 main 의 136/137 이 머지되자 같은 표·인덱스·컬럼을 두 번 만드는
# 파일이 세 방언 모두에 생겼고, STG 부팅이 "idx_chat_cmd_run already exists" 로 섰다. 사본은
# 지웠고, 사본에만 있던 stdout/stderr_truncated 두 컬럼은 157 이 채운다.
# 아래는 그 자리를 지나는 DB 이력 셋을 `config.py` `run_migrations()` 와 같은 순서(migrator
# 생성 -> reconcile_renamed_migrations -> apply_migrations)로 실제 재부팅한다.
#   빈 DB      처음부터 157 까지
#   main 먼저  main 의 136/137 까지 적용된 DB(STG 와 같은 이력)에 v0.2 가 올라온다
#   v0.2 먼저  10-06~10-08 에 v0.2 사본 137/138 로 적용된 DB. 장부의 137/138 행은
#              migration_rename_repair 2패스가 136/137 로 옮겨 적는다

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
    """주어진 파일 집합만 든 임시 디렉터리로 실제 migrator 를 돌려 그 이력의 DB 를 만든다."""
    from sqloader import SQLiteWrapper, DatabaseMigrator

    history_dir = tmp_path / f"{name}_migrations"
    history_dir.mkdir()
    for filename, body in files.items():
        (history_dir / filename).write_text(body, encoding="utf-8")
    db_path = str(tmp_path / f"{name}.db")
    DatabaseMigrator(SQLiteWrapper(db_name=db_path), str(history_dir), True)
    return db_path


def _main_first_history() -> dict[str, str]:
    """main 의 136/137 까지 — STG 에서 v0.2 부팅이 멈춘 바로 그 자리."""
    return {
        p.name: p.read_text(encoding="utf-8")
        for p in _files("sqlite") if p.name <= MAIN_CHAT_FILES[-1]
    }


def _v02_first_history() -> dict[str, str]:
    """main 의 136/137 대신 v0.2 사본 137/138 로 적용된 이력. 사본은 truncated 두 컬럼을 더 가졌다."""
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
