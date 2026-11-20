"""Query-cost indexes for screen refresh (flowgate.default.0291, NR0003 P2).

NR0003 findings 2/6 identified full scans in documents/events refresh queries.
070_documents_events_perf_indexes.sql addresses them. This suite verifies:

1. Indexes actually exist. A CREATE INDEX in a file does not prove the index
   survives in the database: 027_t487 rebuilt documents with DROP/RENAME and
   silently lost two indexes created by 013/015. NR0003 incorrectly listed
   them as present. Apply every migration in order, then inspect sqlite_master
   rather than grepping files. Future table rebuilds must preserve these indexes.
2. The affected queries choose those indexes. Predicate shape can prevent use
   of an existing index. For each query, check that EXPLAIN QUERY PLAN has no
   SCAN documents and includes the expected index. Queries are copied from real
   callers, with their origins noted; update these cases when callers change.

These checks run on SQLite. MySQL/PostgreSQL variants express the same indexes
in their dialects (see original comments); actual EXPLAIN comparisons require
environments with those engines, as recorded in the TR's remaining checks.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

_SCHEMA_DIR = Path(__file__).resolve().parents[2] / "sql" / "migrations" / "sqlite"

# Indexes added/restored by 070 and their column sets.
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
    """An in-memory database with all migrations applied in order.

    Unlike conftest's all_migrations_db, do not swallow exceptions: silently
    skipping a broken 070 would invalidate this entire suite.
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


# 1. The indexes survive in the database.

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
    """070 declares all five indexes; no later file recreates their names.

    015 also creates idx_documents_doc_type_status, but precedes 070 and was
    already undone by 027; restoring it is exactly 070's purpose. A duplicate
    after 070 would replace its effective definition, silently separating the
    actual columns from those verified here despite 070's IF NOT EXISTS.
    """
    text = (_SCHEMA_DIR / "070_documents_events_perf_indexes.sql").read_text(encoding="utf-8")
    later = [p for p in _migrations() if p.name > "070_documents_events_perf_indexes.sql"]
    for name in _EXPECTED_INDEXES:
        assert f"CREATE INDEX IF NOT EXISTS {name}" in text, f"070 에 {name} 선언이 없다"
        clashes = [p.name for p in later if name in p.read_text(encoding="utf-8")]
        assert clashes == [], f"{name} 을 070 이후의 {clashes} 가 다시 정의한다"


# 2. The affected queries choose their indexes.
#
# (label, source, sql, params, expected index)
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

    NR0003 finding 6 identified this as the fastest-degrading query. Both
    expected improvements come from idx_events_type_doc_event: aggregation
    uses (event_type, doc_id, event_id), and doc_id ordering removes the GROUP BY
    temporary B-tree that grew with the number of event rows.
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
    """The MIN(d.seq) join blamed on a missing doc_review_status index in finding 2.

    It is driven by workflow_return_point_docs and looks up documents through
    ux_documents_doc_id, so it was not a full scan even before 070. Thus 070
    added no standalone doc_review_status index. A changed join order would
    break this assertion and require reconsidering that decision.
    """
    sql = (
        "SELECT MIN(d.seq) AS min_seq FROM workflow_return_point_docs s "
        "JOIN documents d ON d.doc_id = s.doc_id "
        "WHERE s.return_point_id = ? AND d.doc_review_status = 'pending_review'"
    )
    plan = _plan(db, sql, [1])
    assert "ux_documents_doc_id" in plan, plan
    assert "SCAN documents" not in plan, plan


# 3. Every dialect creates the same index set.

@pytest.mark.parametrize("dialect", ["mysql", "postgres"])
def test_dialect_migrations_declare_the_same_indexes(dialect):
    """An omitted dialect index would silently slow only that engine.

    Compare names only: column notation differs by dialect, including MySQL's
    prefix length 191 for TEXT doc_review_status.
    """
    path = (
        _SCHEMA_DIR.parent / dialect / "070_documents_events_perf_indexes.sql"
    )
    text = path.read_text(encoding="utf-8")
    for name in _EXPECTED_INDEXES:
        assert f"CREATE INDEX IF NOT EXISTS {name}" in text, f"{dialect}: {name} 누락"
    assert "BEGIN;" not in text, f"{dialect}: DDL 은 암묵 커밋이라 명시 트랜잭션을 쓰지 않는다"
