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

_SERVER_DIR = Path(__file__).resolve().parents[1]
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
    sets = {d: {p.name for p in _files(d)} for d in DIALECTS}
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

def test_every_sqlite_migration_applies_in_name_order():
    """정렬 순서 그대로 전부 적용해 본다.

    번호를 바꾼다는 것은 적용 순서를 건드릴 수 있다는 뜻이다(예: tokens 는 042a·062a·
    064·075 에서 네 번 재생성되고, 그 사이의 ADD COLUMN 이 앞뒤로 밀리면 재생성이
    없는 열을 SELECT 하게 된다). 예외를 삼키지 않고 돌려서 그 사고를 여기서 잡는다.
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


# ── 7. 장부 이관이 실제로 동작하는가 ──────────────────────────────────────────

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
        # 새 설치. 표가 없으므로 옮길 것도 없고, 표를 만들어서도 안 된다 —
        # sqloader 가 곧이어 자기 손으로 만든다.
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
        # 운영자가 새 파일을 손으로 적용했거나, 이전 이관이 중간에 끊긴 경우.
        # 파일당 한 행이라는 상태를 되찾아야 한다.
        old, new = RENAMES[0]
        path = self._make_db(tmp_path, [old, new])

        assert apply_migration_renames("sqlite3", sqlite_path=path) == 1
        assert self._names(path) == {new}

    def test_a_db_that_never_saw_the_old_names_is_untouched(self, tmp_path):
        # 새 이름으로 처음부터 적용된 DB. `news`는 중복될 수 있다(수렴 항목) —
        # 파일 하나에 행 하나이므로 집합으로 정리해서 만든다.
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

LEDGER_FINAL_NAME = "086a_tr_commit_ledger.sql"
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
