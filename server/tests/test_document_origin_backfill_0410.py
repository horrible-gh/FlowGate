"""flowgate.default.0410 TR0009 rev2 — 082_document_origin_backfill.sql 회귀 테스트.

081 은 `documents.origin_provider_name` / `origin_ai_run_id` 두 열을 더하기만 했다.
그래서 열이 생긴 뒤에 만들어진 문서만 작성 AI 를 갖고, 이미 있던 문서는 계속 NULL
이라 화면의 [작성자] 칸에는 등록 계정 이름(개발기에서는 'test')만 보였다.

082 는 이미 저장된 실행 기록(`ai_invoke_runs.reached_doc_ids` = 그 실행이 만들어 낸
문서 목록)에서 같은 행의 provider_name / run_id 를 그대로 옮겨 적는다. 이 파일은 그
UPDATE 문 자체를 파일에서 읽어 실행하며 다음을 고정한다:

  1. 실행이 만든 legacy 문서는 그 실행의 공급자 이름과 run_id 로 채워진다.
  2. 이미 스냅샷이 찍힌 문서는 덮어쓰지 않는다(한쪽 열만 차 있어도 손대지 않는다).
  3. 어떤 실행도 만들지 않은 문서(사람 작성)는 NULL 로 남는다.
  4. provider_name 이 비었거나 NULL 인 실행은 후보가 아니다(이름을 추측하지 않는다).
  5. 한 문서를 여러 실행이 신고하면 먼저 마감된 실행이 이긴다.
  6. 두 번 돌려도 아무 행도 더 바뀌지 않는다(idempotent).
  7. 세 방언 파일이 같은 규칙을 담는다(mysql 만 CONCAT, 나머지는 '||').
"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

os.environ["TESTING"] = "1"
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")

_SERVER_DIR = Path(__file__).resolve().parents[1]
_MIGRATIONS = _SERVER_DIR / "sql" / "migrations"
_SQLITE_DIR = _MIGRATIONS / "sqlite"
_BACKFILL_NAME = "082_document_origin_backfill.sql"
sys.path.insert(0, str(_SERVER_DIR))

_NOW = datetime.now(timezone.utc).isoformat()


def _backfill_statement(dialect: str = "sqlite") -> str:
    """Read the shipped migration and return its single UPDATE statement.

    Reading the file (instead of restating the SQL here) is the point: the test
    fails if the shipped migration drifts from what it is asserted to do.
    """
    text = (_MIGRATIONS / dialect / _BACKFILL_NAME).read_text(encoding="utf-8")
    start = text.find("UPDATE documents")
    assert start != -1, f"{dialect}/{_BACKFILL_NAME} 에 UPDATE 문이 없다"
    end = text.find(";", start) + 1
    return text[start:end]


@pytest.fixture
def conn():
    """Fresh sqlite file with every migration applied, then rows seeded by hand.

    082 runs during this loop too, but on an empty table — every test seeds its own
    rows afterwards and applies the statement itself, so it controls before/after.
    """
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    connection = sqlite3.connect(db_path, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = OFF")
    for migration_file in sorted(_SQLITE_DIR.glob("*.sql")):
        try:
            connection.executescript(migration_file.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 — 이전 방언 잔재 파일은 건너뛴다
            pass
    connection.execute(
        "INSERT OR IGNORE INTO projects (project_id, project_name, created_at, updated_at) "
        "VALUES ('bf410', 'Backfill 0410', ?, ?)",
        (_NOW, _NOW),
    )
    connection.execute(
        "INSERT OR IGNORE INTO groups "
        "(group_id, project_id, module, title, status, created_at, updated_at) "
        "VALUES ('bf410.default.0001', 'bf410', 'default', 'G', 'OPEN', ?, ?)",
        (_NOW, _NOW),
    )
    connection.commit()
    yield connection
    connection.close()
    os.unlink(db_path)


def _add_doc(conn, doc_id: str, seq: int, provider=None, run_id=None) -> str:
    conn.execute(
        "INSERT INTO documents "
        "(doc_id, project_id, module, group_id, type_code, seq, title, status, "
        " created_at, updated_at, origin_provider_name, origin_ai_run_id) "
        "VALUES (?, 'bf410', 'default', 'bf410.default.0001', 'T', ?, 'D', 'open', ?, ?, ?, ?)",
        (doc_id, seq, _NOW, _NOW, provider, run_id),
    )
    conn.commit()
    return doc_id


def _add_run(conn, run_id: str, provider, reached, finished: str) -> str:
    conn.execute(
        "INSERT INTO ai_invoke_runs "
        "(run_id, group_id, project_id, doc_ref, mode, status, provider_name, "
        " reached_doc_ids, started_at, finished_at, created_at, updated_at) "
        "VALUES (?, 'bf410.default.0001', 'bf410', 'bf410.default.0001.0001-R', "
        " 'continuous', 'finished', ?, ?, ?, ?, ?, ?)",
        (run_id, provider, reached, finished, finished, _NOW, _NOW),
    )
    conn.commit()
    return run_id


def _origin(conn, doc_id: str) -> tuple:
    row = conn.execute(
        "SELECT origin_provider_name, origin_ai_run_id FROM documents WHERE doc_id = ?",
        (doc_id,),
    ).fetchone()
    return (row["origin_provider_name"], row["origin_ai_run_id"])


def _run_backfill(conn) -> int:
    cur = conn.execute(_backfill_statement())
    conn.commit()
    return cur.rowcount


class TestOriginBackfill:
    def test_legacy_document_gets_the_provider_of_the_run_that_made_it(self, conn):
        doc = _add_doc(conn, "bf410.default.0001.0002-N", 2)
        _add_run(conn, "aiv_bf_1", "Claude Sonnet 5",
                 '["bf410.default.0001.0002-N"]', "2026-08-12T18:09:23+09:00")
        assert _origin(conn, doc) == (None, None)

        assert _run_backfill(conn) == 1
        assert _origin(conn, doc) == ("Claude Sonnet 5", "aiv_bf_1")

    def test_existing_snapshot_is_never_overwritten(self, conn):
        doc = _add_doc(conn, "bf410.default.0001.0003-NR", 3,
                       provider="Codex GPT-5.6 Sol", run_id="aiv_original")
        _add_run(conn, "aiv_bf_2", "Claude Opus 5",
                 '["bf410.default.0001.0003-NR"]', "2026-08-12T19:00:00+09:00")

        _run_backfill(conn)
        assert _origin(conn, doc) == ("Codex GPT-5.6 Sol", "aiv_original")

    def test_half_filled_row_is_left_alone(self, conn):
        """run_id 만 있고 이름이 없는 행은 '미상'으로 남는다 — 이름을 추측하지 않는다."""
        doc = _add_doc(conn, "bf410.default.0001.0004-T", 4, run_id="aiv_partial")
        _add_run(conn, "aiv_bf_3", "Claude Opus 5",
                 '["bf410.default.0001.0004-T"]', "2026-08-12T19:10:00+09:00")

        _run_backfill(conn)
        assert _origin(conn, doc) == (None, "aiv_partial")

    def test_document_no_run_reached_stays_null(self, conn):
        """사람이 만든 문서는 그대로 NULL — 사람 작성을 AI 로 오표시하지 않는다."""
        doc = _add_doc(conn, "bf410.default.0001.0005-WP", 5)
        _add_run(conn, "aiv_bf_4", "Claude Opus 5", "[]", "2026-08-12T19:20:00+09:00")

        assert _run_backfill(conn) == 0
        assert _origin(conn, doc) == (None, None)

    @pytest.mark.parametrize("provider", [None, "", "   "])
    def test_run_without_a_provider_name_is_not_a_candidate(self, conn, provider):
        doc = _add_doc(conn, "bf410.default.0001.0006-T", 6)
        _add_run(conn, "aiv_bf_5", provider,
                 '["bf410.default.0001.0006-T"]', "2026-08-12T19:30:00+09:00")

        assert _run_backfill(conn) == 0
        assert _origin(conn, doc) == (None, None)

    def test_earliest_finished_run_wins(self, conn):
        doc = _add_doc(conn, "bf410.default.0001.0007-TR", 7)
        _add_run(conn, "aiv_bf_late", "Claude Opus 5",
                 '["bf410.default.0001.0007-TR"]', "2026-08-13T10:00:00+09:00")
        _add_run(conn, "aiv_bf_early", "Claude Sonnet 5",
                 '["bf410.default.0001.0007-TR"]', "2026-08-12T09:00:00+09:00")

        _run_backfill(conn)
        assert _origin(conn, doc) == ("Claude Sonnet 5", "aiv_bf_early")

    def test_only_the_named_document_in_a_multi_document_run_is_matched(self, conn):
        made = _add_doc(conn, "bf410.default.0001.0008-T", 8)
        untouched = _add_doc(conn, "bf410.default.0001.0009-TR", 9)
        _add_run(conn, "aiv_bf_6", "Codex GPT-5.6 Sol",
                 '["bf410.default.0001.0008-T"]', "2026-08-13T14:01:35+09:00")

        assert _run_backfill(conn) == 1
        assert _origin(conn, made) == ("Codex GPT-5.6 Sol", "aiv_bf_6")
        assert _origin(conn, untouched) == (None, None)

    def test_second_application_changes_nothing(self, conn):
        doc = _add_doc(conn, "bf410.default.0001.0010-TS", 10)
        _add_run(conn, "aiv_bf_7", "Codex GPT-5.6 Sol",
                 '["bf410.default.0001.0010-TS"]', "2026-08-13T14:44:49+09:00")

        assert _run_backfill(conn) == 1
        before = _origin(conn, doc)
        assert _run_backfill(conn) == 0
        assert _origin(conn, doc) == before


class TestDialectParity:
    """세 방언이 같은 규칙을 담는지. 병합 뒤 한 방언만 고쳐지는 사고를 막는다."""

    def test_all_three_dialects_ship_the_migration(self):
        for dialect in ("sqlite", "postgres", "mysql"):
            assert (_MIGRATIONS / dialect / _BACKFILL_NAME).is_file()

    def test_sqlite_and_postgres_are_identical(self):
        assert _backfill_statement("sqlite") == _backfill_statement("postgres")

    def test_mysql_differs_only_in_string_concatenation(self):
        mysql = _backfill_statement("mysql")
        # MySQL 은 기본 모드에서 '||' 를 OR 로 읽는다 — 그래서 CONCAT 이어야 한다.
        assert "CONCAT('%\"', documents.doc_id, '\"%')" in mysql
        assert "||" not in mysql
        rejoined = mysql.replace(
            "CONCAT('%\"', documents.doc_id, '\"%')",
            "'%\"' || documents.doc_id || '\"%'",
        )
        assert rejoined == _backfill_statement("sqlite")

    def test_every_dialect_guards_already_filled_rows(self):
        for dialect in ("sqlite", "postgres", "mysql"):
            stmt = _backfill_statement(dialect)
            assert "WHERE origin_provider_name IS NULL" in stmt
            assert "AND origin_ai_run_id IS NULL" in stmt
