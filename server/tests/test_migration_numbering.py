"""Global guards for the migration file set (flowgate.default.0394 T0004, 권고 2).

NR0003 §5.3 의 지적이 이 파일의 존재 이유다. 마이그레이션 번호 중복은 8묶음이었는데
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


def test_the_rename_ledger_maps_one_to_one():
    olds = [old for old, _ in RENAMES]
    news = [new for _, new in RENAMES]
    assert len(set(olds)) == len(olds), "장부에 같은 옛 이름이 두 번 있다"
    assert len(set(news)) == len(news), "장부에 같은 새 이름이 두 번 있다"
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
        # 새 이름으로 처음부터 적용된 DB.
        news = [new for _, new in RENAMES]
        path = self._make_db(tmp_path, news)

        assert apply_migration_renames("sqlite3", sqlite_path=path) == 0
        assert self._names(path) == set(news)
