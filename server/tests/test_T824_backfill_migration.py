"""T826 — Pytest regression tests for T824 backfill migration.

Tests that 034_t824_backfill_null_doc_review_status.sql correctly:
  1. Sets doc_review_status = 'pending_review' for legacy NULL non-M docs (NR/TR/DS)
  2. Sets doc_review_status = 'pending_review' for R rows with decided workflow_steps
  3. Preserves R rows with NULL/undecided workflow_steps (stays NULL)
  4. Preserves M rows unchanged (defensive guard)
  5. Preserves rows that already have a valid doc_review_status value
  6. Is idempotent (second application changes zero rows)
  7. Only produces values in the CHECK enum from migration 027
  8. Handles empty-string doc_review_status ('' → 'pending_review')

Infrastructure pattern:
  - `migrated_conn` fixture: fresh in-memory sqlite3 per test (function scope),
    all migrations 001-033 applied via executescript with try/except (same
    approach as test_T823_block_null_status.py).
  - `_apply_t824(conn)`: reads 034_t824_backfill_null_doc_review_status.sql,
    extracts the single UPDATE statement, executes it, returns rowcount.
  - `_insert_doc(conn, **kwargs)`: minimal INSERT into documents; each test
    seeds its own isolated rows in its own fresh in-memory DB.
"""
from __future__ import annotations

import itertools
import sqlite3
from pathlib import Path

import pytest

_SERVER_DIR = Path(__file__).resolve().parents[1]
_SCHEMA_DIR = _SERVER_DIR / "sql" / "migrations" / "sqlite"
_T824_MIGRATION = _SCHEMA_DIR / "034_t824_backfill_null_doc_review_status.sql"

# CHECK enum values introduced by migration 027 (t487_workflow_review_status)
_VALID_STATUSES = frozenset(
    {"pending_review", "approved", "rejected", "revised", "wf_in_progress", "wf_done"}
)

# Global monotonic counter so doc_ids are unique across the whole session.
_counter: itertools.count = itertools.count(1)


# ── Schema helpers ────────────────────────────────────────────────────────────

def _apply_migrations(conn: sqlite3.Connection, *, stop_before: str | None = None) -> None:
    """Execute migration .sql files in order.

    stop_before: if set, stop before the first file whose name >= that string.
    FK constraints are turned off before loading (seeding needs to bypass FKs).
    """
    conn.execute("PRAGMA foreign_keys = OFF")
    for sql_file in sorted(_SCHEMA_DIR.glob("*.sql")):
        if stop_before and sql_file.name >= stop_before:
            break
        try:
            conn.executescript(sql_file.read_text(encoding="utf-8"))
        except Exception:
            pass
    # Keep FK off so _insert_doc can omit FK dependencies (projects, groups, etc.)
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.commit()


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def migrated_conn():
    """Fresh in-memory sqlite3 connection with migrations 001-033 applied.

    Function scope guarantees no cross-test state leakage.
    Migration 034 (T824) is intentionally NOT applied — each test calls
    _apply_t824() itself so it controls the before/after state.
    """
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    _apply_migrations(conn, stop_before="034")
    yield conn
    conn.close()


# ── Data helpers ──────────────────────────────────────────────────────────────

def _apply_t824(conn: sqlite3.Connection) -> int:
    """Read the T824 migration file (server/sql/migrations/sqlite/034_…sql),
    extract the single UPDATE statement, execute it, and return rowcount.

    Extracting the UPDATE (rather than using executescript) lets callers
    inspect sqlite3.Cursor.rowcount to test idempotency.
    """
    sql_text = _T824_MIGRATION.read_text(encoding="utf-8")
    # The migration file contains exactly one DML statement.
    start = sql_text.find("UPDATE documents")
    end = sql_text.find(";", start) + 1
    update_sql = sql_text[start:end]
    cur = conn.execute(update_sql)
    conn.commit()
    return cur.rowcount


def _insert_doc(conn: sqlite3.Connection, **kwargs) -> str:
    """Insert a minimal row into the documents table; kwargs override defaults.

    Returns the doc_id used.  FK constraints are assumed OFF (set by
    _apply_migrations / migrated_conn fixture) so project/group rows are not
    required.
    """
    n = next(_counter)
    now = "2025-01-01T00:00:00.000Z"
    params: dict = {
        "doc_id": f"t826-{n:06d}",
        "project_id": "t826prj",
        "module": "__ALL__",
        "group_id": None,
        "type_code": "NR",
        "seq": n,
        "title": f"T826 test doc {n}",
        "status": "draft",
        "created_at": now,
        "updated_at": now,
        "doc_review_status": None,
        "workflow_steps": None,
    }
    params.update(kwargs)
    conn.execute(
        "INSERT INTO documents"
        "  (doc_id, project_id, module, group_id, type_code, seq, title,"
        "   status, created_at, updated_at, doc_review_status, workflow_steps)"
        " VALUES"
        "  (:doc_id, :project_id, :module, :group_id, :type_code, :seq,"
        "   :title, :status, :created_at, :updated_at,"
        "   :doc_review_status, :workflow_steps)",
        params,
    )
    conn.commit()
    return params["doc_id"]


def _get_status(conn: sqlite3.Connection, doc_id: str) -> str | None:
    """Return doc_review_status for a given doc_id, or None if not found."""
    row = conn.execute(
        "SELECT doc_review_status FROM documents WHERE doc_id = ?", [doc_id]
    ).fetchone()
    return row[0] if row else None


# ── Test cases ────────────────────────────────────────────────────────────────

def test_backfill_updates_non_r_null_to_pending_review(migrated_conn):
    """Case 1 — NR/TR/DS rows with NULL doc_review_status → 'pending_review'.

    Verifies the main backfill path for non-M, non-R legacy documents.
    """
    conn = migrated_conn
    nr_id = _insert_doc(conn, type_code="NR", doc_review_status=None)
    ds_id = _insert_doc(conn, type_code="DS", doc_review_status=None)
    tr_id = _insert_doc(conn, type_code="TR", doc_review_status=None)

    _apply_t824(conn)

    assert _get_status(conn, nr_id) == "pending_review", \
        f"NR NULL status was not backfilled; got {_get_status(conn, nr_id)!r}"
    assert _get_status(conn, ds_id) == "pending_review", \
        f"DS NULL status was not backfilled; got {_get_status(conn, ds_id)!r}"
    assert _get_status(conn, tr_id) == "pending_review", \
        f"TR NULL status was not backfilled; got {_get_status(conn, tr_id)!r}"


def test_backfill_updates_r_with_decided_workflow(migrated_conn):
    """Case 2 — R row with non-empty workflow_steps and NULL status → 'pending_review'.

    The migration predicate:
      type_code = 'R' AND workflow_steps IS NOT NULL
      AND workflow_steps != '[]' AND workflow_steps != ''
    means R docs whose workflow HAS been decided are treated like any other doc.
    """
    conn = migrated_conn
    r_id = _insert_doc(
        conn,
        type_code="R",
        doc_review_status=None,
        workflow_steps='["NR","TR"]',
    )

    _apply_t824(conn)

    assert _get_status(conn, r_id) == "pending_review", \
        f"R doc with decided workflow not backfilled; got {_get_status(conn, r_id)!r}"


def test_backfill_preserves_r_with_undecided_workflow(migrated_conn):
    """Case 3 — R row with NULL workflow_steps stays NULL.

    Policy (M026 §8-1): R docs shown as [—] until PM decides workflow.
    The migration must NOT touch them.
    """
    conn = migrated_conn
    r_id = _insert_doc(conn, type_code="R", doc_review_status=None, workflow_steps=None)

    _apply_t824(conn)

    assert _get_status(conn, r_id) is None, \
        f"R doc with undecided workflow was wrongly backfilled; got {_get_status(conn, r_id)!r}"


def test_backfill_preserves_m_rows(migrated_conn):
    """Case 4 — M rows are untouched regardless of their current status.

    Migration 033 already handled M backfill; the T824 migration has an
    explicit 'type_code != M' guard as a belt-and-suspenders check.
    """
    conn = migrated_conn
    m_null_id = _insert_doc(conn, type_code="M", doc_review_status=None)
    m_approved_id = _insert_doc(conn, type_code="M", doc_review_status="approved")

    _apply_t824(conn)

    assert _get_status(conn, m_null_id) is None, \
        f"M row with NULL status was wrongly modified; got {_get_status(conn, m_null_id)!r}"
    assert _get_status(conn, m_approved_id) == "approved", \
        f"M row with 'approved' status was wrongly modified; got {_get_status(conn, m_approved_id)!r}"


def test_backfill_preserves_already_valid_status(migrated_conn):
    """Case 5 — Rows with any existing valid status are not overwritten.

    The migration WHERE predicate is 'IS NULL OR = ""'; all enum values
    must pass through unchanged.
    """
    conn = migrated_conn
    valid = list(_VALID_STATUSES)
    ids = {s: _insert_doc(conn, type_code="NR", doc_review_status=s) for s in valid}

    _apply_t824(conn)

    for status, doc_id in ids.items():
        assert _get_status(conn, doc_id) == status, \
            f"Row with pre-existing status {status!r} was changed to {_get_status(conn, doc_id)!r}"


def test_backfill_is_idempotent(migrated_conn):
    """Case 6 — Applying the migration twice; second run changes zero rows.

    After the first run all previously-NULL targets become 'pending_review'
    (a valid enum value), so the second run's WHERE clause matches nothing.
    """
    conn = migrated_conn
    _insert_doc(conn, type_code="NR", doc_review_status=None)
    _insert_doc(conn, type_code="DS", doc_review_status=None)

    _apply_t824(conn)           # first application — changes 2 rows
    changed = _apply_t824(conn) # second application — should change 0

    assert changed == 0, \
        f"Migration is not idempotent: second application changed {changed} rows"


def test_backfill_honors_check_constraint(migrated_conn):
    """Case 7 — Every value written by the migration is in the migration-027 CHECK enum.

    Verifies that 'pending_review' is the only value the migration assigns,
    and that it is a member of the valid CHECK set.
    """
    conn = migrated_conn
    for type_code in ("NR", "DS", "N", "A", "TR"):
        _insert_doc(conn, type_code=type_code, doc_review_status=None)

    _apply_t824(conn)

    rows = conn.execute(
        "SELECT doc_review_status FROM documents WHERE doc_review_status IS NOT NULL"
    ).fetchall()
    assert rows, "Expected at least one updated row"
    for row in rows:
        assert row[0] in _VALID_STATUSES, \
            f"doc_review_status {row[0]!r} is outside the migration-027 CHECK enum"


def test_backfill_updates_empty_string_status():
    """Case 8 — doc_review_status = '' (empty string) → 'pending_review'.

    Empty-string rows pre-date migration 027's CHECK constraint; they exist
    only in legacy databases.  This test inserts the row with CHECK enforcement
    temporarily disabled (SQLite ≥ 3.41 PRAGMA ignore_check_constraints) to
    simulate legacy state, then verifies that the T824 migration's
    'IS NULL OR = ''' predicate handles them correctly.

    Uses a standalone connection (not the migrated_conn fixture) so the
    schema-loading phase can be controlled independently.
    """
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        _apply_migrations(conn, stop_before="034")

        n = next(_counter)
        now = "2025-01-01T00:00:00.000Z"
        doc_id = f"t826-es-{n:06d}"

        # Bypass CHECK to plant a legacy empty-string row
        conn.execute("PRAGMA ignore_check_constraints = ON")
        conn.execute(
            "INSERT INTO documents"
            "  (doc_id, project_id, module, group_id, type_code, seq, title,"
            "   status, created_at, updated_at, doc_review_status, workflow_steps)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            [doc_id, "t826prj", "__ALL__", None, "NR", n,
             "Empty-string legacy doc", "draft", now, now, "", None],
        )
        conn.execute("PRAGMA ignore_check_constraints = OFF")
        conn.commit()

        _apply_t824(conn)

        assert _get_status(conn, doc_id) == "pending_review", \
            f"Empty-string row not backfilled; got {_get_status(conn, doc_id)!r}"
    finally:
        conn.close()
