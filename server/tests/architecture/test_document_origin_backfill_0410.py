"""regressions for 082_document_origin_backfill.sql.

081 only added documents.origin_provider_name and origin_ai_run_id. Older
documents retained NULL, so the author column showed their registered account
(test in development) rather than the authoring AI.

082 copies provider_name/run_id from existing ai_invoke_runs records whose
reached_doc_ids list the created documents. Execute the actual UPDATE from the
file and verify: legacy AI documents gain their provider/run snapshots; existing
snapshots are not overwritten even if only one column is populated; human-authored
documents remain NULL; empty/NULL provider names are ineligible and never guessed;
the earliest completed run wins when multiple runs claim a document; repeat
execution is idempotent; and all three dialects use the same rule (MySQL CONCAT,
otherwise ||).
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

_SERVER_DIR = Path(__file__).resolve().parents[2]
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
        except Exception:  # noqa: BLE001 - Skip leftover files from older dialect layouts.
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
        """A row with run_id but no name stays unknown; never invent a name."""
        doc = _add_doc(conn, "bf410.default.0001.0004-T", 4, run_id="aiv_partial")
        _add_run(conn, "aiv_bf_3", "Claude Opus 5",
                 '["bf410.default.0001.0004-T"]', "2026-08-12T19:10:00+09:00")

        _run_backfill(conn)
        assert _origin(conn, doc) == (None, "aiv_partial")

    def test_document_no_run_reached_stays_null(self, conn):
        """Human-authored documents retain NULL rather than being mislabeled as AI work."""
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
    """All dialects share the same rules, preventing one-sided post-merge fixes."""

    def test_all_three_dialects_ship_the_migration(self):
        for dialect in ("sqlite", "postgres", "mysql"):
            assert (_MIGRATIONS / dialect / _BACKFILL_NAME).is_file()

    def test_sqlite_and_postgres_are_identical(self):
        assert _backfill_statement("sqlite") == _backfill_statement("postgres")

    def test_mysql_differs_only_in_string_concatenation(self):
        mysql = _backfill_statement("mysql")
        # MySQL's default mode interprets || as OR, so CONCAT is required.
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
