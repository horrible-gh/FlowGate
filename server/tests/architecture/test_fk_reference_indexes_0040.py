"""FOREIGN KEY reference indexes, migration 194 (flowgate.v02test.0040 0003-T).

A module identifier change rewrites document and group ids in one transaction and, at COMMIT,
runs the FK check of every changed id against every referencing table. A referencing column that
leads no index makes each of those checks a full scan (11/17: ~2 minutes of a 3-minute global lock).
194 indexes the columns that had none.

What this suite fixes:
1. The indexes exist in a DB built from every SQLite migration in order (not a grep of the file:
   027 once dropped indexes silently by rebuilding a table).
2. Every FOREIGN KEY to documents/groups is found by an index lookup, not a table scan. This also
   catches the next table that adds such an FK without an index, or a rebuild that drops one.
3. The real runner (sqloader DatabaseMigrator) applies 194 to an already-migrated DB as a later boot.
4. The postgres and mysql files declare the matching indexes (no engine runs here; static check).
"""
from __future__ import annotations

import re
import shutil
import sqlite3
from pathlib import Path

import pytest

_MIGRATIONS = Path(__file__).resolve().parents[2] / "sql" / "migrations"
_MIGRATION = "194_fk_reference_indexes.sql"

# index name -> (table, column). All eleven exist in sqlite and postgres.
_EXPECTED = {
    "idx_ai_invoke_paused_chains_doc_ref": ("ai_invoke_paused_chains", "doc_ref"),
    "idx_attachment_upload_requests_group_id": ("attachment_upload_requests", "group_id"),
    "idx_document_mention_copies_doc_id": ("document_mention_copies", "doc_id"),
    "idx_events_related_doc_id": ("events", "related_doc_id"),
    "idx_review_dry_run_receipts_group_id": ("review_dry_run_receipts", "group_id"),
    "idx_test_runs_tsr_doc_id": ("test_runs", "tsr_doc_id"),
    "idx_tokens_group_id": ("tokens", "group_id"),
    "idx_tr_self_check_runs_group_id": ("tr_self_check_runs", "group_id"),
    "idx_user_chat_source_access_group_id": ("user_chat_source_access", "group_id"),
    "idx_workflow_return_point_docs_doc_id": ("workflow_return_point_docs", "doc_id"),
    "idx_workflow_sequence_items_source_doc_id": ("workflow_sequence_items", "source_doc_id"),
}

# MySQL: InnoDB already indexed these table-level FOREIGN KEYs itself (migration that declares it).
_MYSQL_AUTO_INDEXED = {
    "idx_attachment_upload_requests_group_id": "181_attachment_upload_requests.sql",
    "idx_review_dry_run_receipts_group_id": "112_review_dry_run_receipts.sql",
    "idx_tr_self_check_runs_group_id": "127_tr_self_check_runs.sql",
    "idx_user_chat_source_access_group_id": "177_group_chat_source_access.sql",
    "idx_workflow_return_point_docs_doc_id": "053_workflow_return_points.sql",
}


def _executable_sql(path: Path) -> str:
    body = re.sub(r"/\*.*?\*/", " ", path.read_text(encoding="utf-8"), flags=re.DOTALL)
    body = "\n".join(re.sub(r"--.*$", "", line) for line in body.splitlines())
    return " ".join(body.split())


def _created_indexes(sql: str) -> dict[str, tuple[str, str]]:
    found = re.findall(
        r"CREATE INDEX (?:IF NOT EXISTS )?(\w+) ON (\w+) \((\w+)\);", sql, flags=re.IGNORECASE)
    return {name: (table, column) for name, table, column in found}


@pytest.fixture(scope="module")
def db() -> sqlite3.Connection:
    """Every SQLite migration in order. Errors are not swallowed: a broken 194 must fail here."""
    conn = sqlite3.connect(":memory:")
    for path in sorted((_MIGRATIONS / "sqlite").glob("*.sql")):
        conn.executescript(path.read_text(encoding="utf-8"))
    conn.execute("ANALYZE")
    yield conn
    conn.close()


def _reference_fks(conn: sqlite3.Connection) -> list[tuple[str, str, str]]:
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    fks = []
    for table in tables:
        for row in conn.execute(f"PRAGMA foreign_key_list('{table}')"):
            if row[2] in ("documents", "groups"):
                fks.append((table, row[3], row[2]))
    return fks


# ── 1. the indexes are in the migrated DB ──────────────────────────────────────

@pytest.mark.parametrize("name", sorted(_EXPECTED))
def test_index_exists_on_expected_column(db, name):
    table, column = _EXPECTED[name]
    row = db.execute(
        "SELECT tbl_name FROM sqlite_master WHERE type = 'index' AND name = ?", [name]).fetchone()
    assert row is not None, f"{name} missing after all migrations"
    assert row[0] == table
    assert [r[2] for r in db.execute(f"PRAGMA index_info('{name}')")] == [column]


# ── 2. every FK to documents/groups is an index lookup ─────────────────────────

def test_the_known_slow_references_are_foreign_keys(db):
    fks = set(_reference_fks(db))
    for table, column, parent in (
        ("events", "related_doc_id", "documents"),
        ("workflow_sequence_items", "source_doc_id", "documents"),
        ("tokens", "group_id", "groups"),
    ):
        assert (table, column, parent) in fks


def test_every_reference_to_documents_and_groups_uses_an_index(db):
    fks = _reference_fks(db)
    assert len(fks) >= 50, f"FK discovery looks broken: {len(fks)} FKs"
    scans = []
    for table, column, parent in fks:
        plan = " | ".join(str(r[-1]) for r in db.execute(
            f'EXPLAIN QUERY PLAN SELECT 1 FROM "{table}" WHERE "{column}" = ?', ["x"]))
        if "USING" not in plan or f"SCAN {table}" in plan:
            scans.append(f"{table}.{column} -> {parent}: {plan}")
    assert scans == [], "FK columns without an index:\n" + "\n".join(scans)


# ── 3. the real runner applies 194 on the next boot ───────────────────────────

def test_sqloader_applies_194_to_a_db_migrated_through_193(tmp_path):
    """A deployed DB has 001..193 in its ledger; the boot that ships 194 applies only 194."""
    migrator = pytest.importorskip("sqloader.migrator")
    wrapper = pytest.importorskip("sqloader.sqlite3")
    seed = tmp_path / "migrations"
    seed.mkdir()
    for path in sorted((_MIGRATIONS / "sqlite").glob("*.sql")):
        if path.name != _MIGRATION:
            shutil.copy(path, seed / path.name)
    db_path = tmp_path / "deployed.db"
    migrator.DatabaseMigrator(wrapper.SQLiteWrapper(str(db_path)), str(seed), auto_run=True)

    def state():
        conn = sqlite3.connect(str(db_path))
        try:
            names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'")}
            ledger = {r[0] for r in conn.execute("SELECT filename FROM migrations")}
        finally:
            conn.close()
        return names, ledger

    names, ledger = state()
    assert not (set(_EXPECTED) & names), "194 indexes must not exist before 194 is on disk"
    assert _MIGRATION not in ledger

    shutil.copy(_MIGRATIONS / "sqlite" / _MIGRATION, seed / _MIGRATION)
    migrator.DatabaseMigrator(wrapper.SQLiteWrapper(str(db_path)), str(seed), auto_run=True)
    names, ledger = state()
    assert set(_EXPECTED) <= names
    assert _MIGRATION in ledger


# ── 4. dialect files ───────────────────────────────────────────────────────────

def test_each_dialect_ships_194_once():
    for dialect in ("sqlite", "postgres", "mysql"):
        assert (_MIGRATIONS / dialect / _MIGRATION).is_file(), f"missing {dialect}/{_MIGRATION}"
        assert [p.name for p in (_MIGRATIONS / dialect).glob("194_*.sql")] == [_MIGRATION], dialect


@pytest.mark.parametrize("dialect", ["sqlite", "postgres"])
def test_sqlite_and_postgres_declare_all_eleven(dialect):
    path = _MIGRATIONS / dialect / _MIGRATION
    sql = _executable_sql(path)
    assert _created_indexes(sql) == _EXPECTED
    assert sql.count("CREATE INDEX IF NOT EXISTS") == len(_EXPECTED)
    assert "CONCURRENTLY" not in sql.upper(), "the migrator may run the file inside a transaction"
    assert "BEGIN" not in sql.upper()
    assert "-- DOWN:" in path.read_text(encoding="utf-8")


def test_mysql_skips_only_the_innodb_auto_indexed_foreign_keys():
    path = _MIGRATIONS / "mysql" / _MIGRATION
    sql = _executable_sql(path)
    expected = {k: v for k, v in _EXPECTED.items() if k not in _MYSQL_AUTO_INDEXED}
    assert _created_indexes(sql) == expected
    assert "IF NOT EXISTS" not in sql.upper(), "MySQL 8 has no CREATE INDEX IF NOT EXISTS"
    assert "-- DOWN:" in path.read_text(encoding="utf-8")


@pytest.mark.parametrize("name", sorted(_MYSQL_AUTO_INDEXED))
def test_mysql_skipped_columns_have_a_table_level_foreign_key(name):
    """InnoDB adds an index only for a table-level FOREIGN KEY; inline REFERENCES is discarded."""
    _, column = _EXPECTED[name]
    sql = _executable_sql(_MIGRATIONS / "mysql" / _MYSQL_AUTO_INDEXED[name])
    assert re.search(
        rf"FOREIGN KEY \({column}\) REFERENCES (documents|groups)\(", sql), name
