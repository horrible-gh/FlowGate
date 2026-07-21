"""Query-cost indexes for the screen-refresh path (flowgate.default.0291, NR0003 P2).

NR0003 발견 2/6 은 화면 갱신 경로의 documents/events 조회 여러 건이 인덱스를 못 타고
전체 스캔한다고 지적했다. 070_documents_events_perf_indexes.sql 이 그 자리를 덮는다.

이 스위트가 고정하는 것은 두 가지다.

1. **인덱스가 실제로 존재한다** — 마이그레이션 파일에 CREATE INDEX 가 적혀 있다는 것과
   DB 에 인덱스가 남는다는 것은 다르다. 027_t487 이 documents 를 DROP/RENAME 으로
   재생성하면서 013·015 가 만든 인덱스 2개를 조용히 날려 버린 전례가 정확히 이 차이다
   (NR0003 의 "현재 인덱스" 목록이 그 둘을 존재하는 것으로 잘못 적은 이유이기도 하다).
   그래서 파일을 grep 하지 않고 **전 마이그레이션을 순서대로 적용한 뒤 sqlite_master 를
   읽는다.** 나중에 누가 또 테이블을 재생성하면 여기서 걸린다.

2. **문제의 쿼리가 그 인덱스를 고른다** — 인덱스가 있어도 술어 모양이 어긋나면 옵티마이저는
   쓰지 않는다. EXPLAIN QUERY PLAN 에 "SCAN documents" 가 남아 있지 않은지, 그리고
   기대한 인덱스 이름이 잡혔는지를 쿼리별로 확인한다. 쿼리 문자열은 실제 호출부에서 그대로
   가져왔고, 출처를 각 케이스에 적어 두었다 — 호출부가 바뀌면 여기도 함께 고쳐야 한다는
   신호다.

SQLite 전용 검증이다. MySQL/PostgreSQL 판은 같은 인덱스를 방언 문법으로 옮긴 것이고
(원본 주석 참조), 실제 EXPLAIN 비교는 해당 엔진이 붙은 환경에서 해야 한다 — TR 의
"남는 확인" 에 적었다.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

_SCHEMA_DIR = Path(__file__).resolve().parents[1] / "sql" / "migrations" / "sqlite"

# 070 이 추가/복구하는 인덱스와 그 컬럼 구성.
_EXPECTED_INDEXES = {
    "idx_documents_group_seq": ["group_id", "seq"],
    "idx_documents_group_type_review": ["group_id", "type_code", "doc_review_status"],
    "idx_documents_prj_group_updated": ["project_id", "group_id", "updated_at"],
    "idx_documents_doc_type_status": ["type_code", "status"],
    "idx_events_type_doc_event": ["event_type", "doc_id", "event_id"],
}


def _migrations() -> list[Path]:
    return sorted(_SCHEMA_DIR.glob("*.sql"))


@pytest.fixture(scope="module")
def db() -> sqlite3.Connection:
    """전 마이그레이션을 순서대로 적용한 in-memory DB.

    conftest 의 all_migrations_db 와 달리 예외를 삼키지 않는다 — 070 이 깨져도 조용히
    지나가면 이 스위트 전체가 무의미해진다.
    """
    conn = sqlite3.connect(":memory:")
    for path in _migrations():
        conn.executescript(path.read_text(encoding="utf-8"))
    conn.execute("ANALYZE")
    yield conn
    conn.close()


def _index_columns(conn: sqlite3.Connection, index_name: str) -> list[str]:
    return [r[2] for r in conn.execute(f"PRAGMA index_info('{index_name}')")]


def _plan(conn: sqlite3.Connection, sql: str, params: list) -> str:
    rows = conn.execute("EXPLAIN QUERY PLAN " + sql, params).fetchall()
    return "\n".join(str(r[-1]) for r in rows)


# ── 1. 인덱스가 DB 에 남아 있는가 ─────────────────────────────────────────────

@pytest.mark.parametrize("name,columns", sorted(_EXPECTED_INDEXES.items()))
def test_index_exists_with_expected_columns(db, name, columns):
    present = {
        r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type = 'index'")
    }
    assert name in present, (
        f"{name} 이 없다. 070 이후에 documents/events 를 재생성하는 마이그레이션이 "
        f"추가됐다면 그 안에서 이 인덱스도 다시 만들어야 한다 (027 전례)."
    )
    assert _index_columns(db, name) == columns


def test_migration_070_declares_all_five_and_nothing_later_redefines_them():
    """070 이 다섯 개를 전부 선언하고, 070 **이후** 파일이 같은 이름을 다시 만들지 않는다.

    015 도 idx_documents_doc_type_status 를 만들지만 그건 070 앞이고 027 이 이미
    날려 버렸다 — 070 이 복구하는 대상이 정확히 그것이라 여기서 문제가 되지 않는다.
    문제가 되는 것은 070 **뒤에** 같은 이름이 다시 나오는 경우다. 그때는 070 의
    IF NOT EXISTS 가 아니라 나중 파일이 실효 정의가 되어, 이 스위트가 검증하는 컬럼
    구성과 DB 의 실제 구성이 조용히 어긋난다.
    """
    text = (_SCHEMA_DIR / "070_documents_events_perf_indexes.sql").read_text(encoding="utf-8")
    later = [p for p in _migrations() if p.name > "070_documents_events_perf_indexes.sql"]
    for name in _EXPECTED_INDEXES:
        assert f"CREATE INDEX IF NOT EXISTS {name}" in text, f"070 에 {name} 선언이 없다"
        clashes = [p.name for p in later if name in p.read_text(encoding="utf-8")]
        assert clashes == [], f"{name} 을 070 이후의 {clashes} 가 다시 정의한다"


# ── 2. 문제의 쿼리가 그 인덱스를 고르는가 ────────────────────────────────────
#
# (label, 출처, sql, params, 기대 인덱스)
_PLAN_CASES = [
    (
        "get_front_doc",
        "db/workflow_return_points.py get_front_doc()",
        "SELECT doc_id, seq, type_code, title FROM documents WHERE group_id = ? AND seq = ?",
        ["g1", 1],
        "idx_documents_group_seq",
    ),
    (
        "groups_root_wf_done",
        "services/git_service.py _groups_root_wf_done()",
        "SELECT DISTINCT group_id FROM documents WHERE group_id IN (?, ?) "
        "AND type_code IN ('R','B') AND doc_review_status = 'wf_done'",
        ["g1", "g2"],
        "idx_documents_group_type_review",
    ),
    (
        "group_ac_doc_ids",
        "services/git_service.py _group_ac_doc_ids()",
        "SELECT group_id, MAX(doc_id) AS doc_id FROM documents "
        "WHERE group_id IN (?, ?) AND type_code = 'AC' GROUP BY group_id",
        ["g1", "g2"],
        "idx_documents_group_type_review",
    ),
    (
        "get_documents_by_group",
        "db/documents.py get_documents()",
        "SELECT * FROM documents WHERE project_id = ? AND group_id = ? "
        "ORDER BY updated_at DESC LIMIT ? OFFSET ?",
        ["p1", "g1", 100, 0],
        "idx_documents_prj_group_updated",
    ),
    (
        "get_documents_by_status_and_types",
        "db/documents.py get_documents_by_status_and_types()",
        "SELECT * FROM documents WHERE status = ? AND type_code IN (?, ?) "
        "ORDER BY updated_at ASC, id ASC",
        ["open", "R", "B"],
        "idx_documents_doc_type_status",
    ),
]


@pytest.mark.parametrize(
    "label,origin,sql,params,expected",
    _PLAN_CASES,
    ids=[c[0] for c in _PLAN_CASES],
)
def test_query_uses_expected_index(db, label, origin, sql, params, expected):
    plan = _plan(db, sql, params)
    assert expected in plan, f"{origin} 이 {expected} 를 쓰지 않는다:\n{plan}"
    assert "SCAN documents" not in plan, f"{origin} 이 여전히 전체 스캔이다:\n{plan}"


def test_created_memo_files_map_uses_covering_scan_without_temp_groupby(db):
    """db/events.py get_created_memo_files_map_by_project().

    NR0003 발견 6 이 "가장 빨리 나빠지는 쿼리"로 지목한 자리. 기대하는 변화는 두 가지고,
    둘 다 idx_events_type_doc_event 하나에서 나온다:

      - 내부 집계가 (event_type, doc_id, event_id) 인덱스를 탄다.
      - doc_id 로 이미 정렬된 인덱스를 읽으므로 GROUP BY 용 임시 B-tree 가 사라진다.
        이게 events 행수에 비례해 커지던 부분이다.
    """
    sql = (
        "SELECT e.doc_id, e.memo_file FROM events e INNER JOIN ("
        "  SELECT e2.doc_id, MAX(e2.event_id) AS max_event_id FROM events e2"
        "  INNER JOIN documents d ON d.doc_id = e2.doc_id"
        "  WHERE e2.event_type = 'created' AND e2.memo_file IS NOT NULL"
        "    AND e2.memo_file != '' AND d.project_id = ?"
        "  GROUP BY e2.doc_id"
        ") latest ON e.doc_id = latest.doc_id AND e.event_id = latest.max_event_id"
    )
    plan = _plan(db, sql, ["p1"])
    assert "idx_events_type_doc_event" in plan, plan
    assert "TEMP B-TREE FOR GROUP BY" not in plan, plan


def test_return_point_min_seq_was_already_indexed(db):
    """NR0003 발견 2 가 doc_review_status 인덱스 부재 탓으로 돌린 MIN(d.seq) 조인.

    실제로는 workflow_return_point_docs 쪽에서 구동되어 documents 를 ux_documents_doc_id
    로 찍는다 — 070 이전에도 전체 스캔이 아니었다. 그래서 070 은 doc_review_status
    단독 인덱스를 추가하지 않았다. 그 판단을 여기에 고정해 둔다: 이 단언이 깨진다면
    조인 순서가 바뀐 것이고, 그때는 단독 인덱스를 다시 검토해야 한다.
    """
    sql = (
        "SELECT MIN(d.seq) AS min_seq FROM workflow_return_point_docs s "
        "JOIN documents d ON d.doc_id = s.doc_id "
        "WHERE s.return_point_id = ? AND d.doc_review_status = 'pending_review'"
    )
    plan = _plan(db, sql, [1])
    assert "ux_documents_doc_id" in plan, plan
    assert "SCAN documents" not in plan, plan


# ── 3. 방언 판이 같은 인덱스 집합을 만드는가 ─────────────────────────────────

@pytest.mark.parametrize("dialect", ["mysql", "postgres"])
def test_dialect_migrations_declare_the_same_indexes(dialect):
    """방언 판이 인덱스 하나를 빠뜨리면 그 엔진에서만 조용히 느려진다.

    이름 집합만 대조한다 — 컬럼 표기는 방언마다 다르다(MySQL 은 TEXT 인
    doc_review_status 에 prefix 길이 191 이 붙는다).
    """
    path = (
        _SCHEMA_DIR.parent / dialect / "070_documents_events_perf_indexes.sql"
    )
    text = path.read_text(encoding="utf-8")
    for name in _EXPECTED_INDEXES:
        assert f"CREATE INDEX IF NOT EXISTS {name}" in text, f"{dialect}: {name} 누락"
    assert "BEGIN;" not in text, f"{dialect}: DDL 은 암묵 커밋이라 명시 트랜잭션을 쓰지 않는다"
