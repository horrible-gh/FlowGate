"""T600 — DB004 migration tests.

Test procedure:
  1. Apply up to migration 031 (pre-migration snapshot)
  2. Apply migration 032
  3. Verify DB004 post-migration invariants (I-1~I-4)
  4. Verify backfill correctness
  5. Verify idempotence (no error when 032 is re-run)
"""
from __future__ import annotations

import os
import re
import sqlite3
import tempfile
from pathlib import Path

import pytest

_MIGRATIONS_DIR = (
    Path(__file__).resolve().parents[1] / "sql" / "migrations" / "sqlite"
)


# A migration ordinal is three digits, optionally followed by one letter: the
# letter is how a file that arrived second on the same number keeps its position
# in sort order (0394 T0004, e.g. 031a). Read the digits and ignore the letter —
# 031 and 031a are both "up to 031", which is what this suite has always applied.
_ORDINAL = re.compile(r"^(\d+)")


def _ordinal(name: str) -> int:
    match = _ORDINAL.match(name)
    assert match, f"migration file name has no ordinal: {name}"
    return int(match.group(1))


def _apply_migrations_up_to(conn: sqlite3.Connection, up_to: int) -> None:
    """Apply migration files in order up to the given number."""
    files = sorted(_MIGRATIONS_DIR.glob("*.sql"))
    for f in files:
        if _ordinal(f.name) <= up_to:
            conn.executescript(f.read_text(encoding="utf-8"))


def _apply_migration(conn: sqlite3.Connection, number: int) -> None:
    """Apply the migration file for a specific number."""
    files = list(_MIGRATIONS_DIR.glob(f"{number:03d}_*.sql"))
    assert files, f"Migration file {number:03d} is missing"
    conn.executescript(files[0].read_text(encoding="utf-8"))


@pytest.fixture
def pre_migration_db():
    """DB with migrations applied up to 031 (032 not applied)."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    _apply_migrations_up_to(conn, 31)
    yield conn
    conn.close()
    os.unlink(db_path)


@pytest.fixture
def post_migration_db(pre_migration_db):
    """DB with migration 032 applied as well (including backfilled data)."""
    conn = pre_migration_db

    # Insert pre-migration seed data
    conn.executescript("""
        INSERT OR IGNORE INTO projects
            (project_id, project_name, is_active, created_at, updated_at)
            VALUES ('PROJ1', 'Test', 1, '2026-01-01', '2026-01-01');

        INSERT OR IGNORE INTO groups
            (group_id, project_id, module, title, status, created_at, updated_at)
            VALUES ('PROJ1.mod.0001', 'PROJ1', 'mod', 'Test group', 'OPEN', '2026-01-01', '2026-01-01');

        INSERT OR IGNORE INTO documents
            (doc_id, project_id, module, group_id, type_code, seq, title, status, created_at, updated_at)
            VALUES
                ('D001', 'PROJ1', 'mod', 'PROJ1.mod.0001', 'D', 1, 'Parent', 'open', '2026-01-01', '2026-01-01'),
                ('NR001', 'PROJ1', 'mod', 'PROJ1.mod.0001', 'NR', 1, 'Result1', 'open', '2026-01-01', '2026-01-01'),
                ('NR002', 'PROJ1', 'mod', 'PROJ1.mod.0001', 'NR', 2, 'Result2', 'open', '2026-01-01', '2026-01-01');

        INSERT OR IGNORE INTO workflow_sequences
            (id, doc_id, created_at, updated_at)
            VALUES (1, 'D001', '2026-01-01', '2026-01-01');

        INSERT OR IGNORE INTO workflow_sequence_items
            (id, sequence_id, item_seq, type, label, doc_class, sort_order, status, created_at, updated_at)
            VALUES
                (1, 1, 1, 'NR', 'NR Slot 1', 'R', 0, 'done',        '2026-01-01', '2026-01-01'),
                (2, 1, 2, 'TR', 'TR Slot 2', 'R', 1, 'in_progress', '2026-01-01', '2026-01-01'),
                (3, 1, 3, 'NR', 'NR Slot 3', 'R', 2, 'pending',     '2026-01-01', '2026-01-01');

        INSERT OR IGNORE INTO workflow_item_results
            (id, item_id, registered_path, registered_doc_id, status, registered_at, created_at, updated_at)
            VALUES
                (1, 1, '/path/NR001.yaml', 'NR001', 'approved',         '2026-01-01', '2026-01-01', '2026-01-01'),
                (2, 2, '/path/NR002.yaml', 'NR002', 'pending_approval',  '2026-01-01', '2026-01-01', '2026-01-01');
    """)
    conn.commit()

    # Apply migration 032
    _apply_migration(conn, 32)
    yield conn


# ── Column existence tests ────────────────────────────────────────────────────

def test_result_doc_id_column_added(post_migration_db):
    """After applying 032, the result_doc_id column must exist in workflow_sequence_items."""
    cols = {
        r["name"]
        for r in post_migration_db.execute(
            "PRAGMA table_info(workflow_sequence_items)"
        ).fetchall()
    }
    assert "result_doc_id" in cols, "result_doc_id column missing"


def test_result_doc_id_index_created(post_migration_db):
    """The idx_wfseq_items_result_doc index must be created."""
    indexes = {
        r["name"]
        for r in post_migration_db.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        ).fetchall()
    }
    assert "idx_wfseq_items_result_doc" in indexes, "index missing"


# ── Backfill tests ────────────────────────────────────────────────────────────

def test_backfill_fills_done_item(post_migration_db):
    """The done-state item (id=1) must be filled with NR001 based on workflow_item_results."""
    row = post_migration_db.execute(
        "SELECT result_doc_id FROM workflow_sequence_items WHERE id = 1"
    ).fetchone()
    assert row is not None
    assert row["result_doc_id"] == "NR001", f"expected NR001, got {row['result_doc_id']}"


def test_backfill_fills_in_progress_item(post_migration_db):
    """The in_progress item (id=2) must also be filled if it has a result."""
    row = post_migration_db.execute(
        "SELECT result_doc_id FROM workflow_sequence_items WHERE id = 2"
    ).fetchone()
    assert row is not None
    assert row["result_doc_id"] == "NR002", f"expected NR002, got {row['result_doc_id']}"


def test_backfill_leaves_pending_null(post_migration_db):
    """The pending item (id=3) with no result must remain NULL."""
    row = post_migration_db.execute(
        "SELECT result_doc_id FROM workflow_sequence_items WHERE id = 3"
    ).fetchone()
    assert row is not None
    assert row["result_doc_id"] is None, f"expected NULL, got {row['result_doc_id']}"


def test_backfill_count_invariant(post_migration_db):
    """filled_items >= wir_distinct_items invariant (DB004 §4 step 2 verification query)."""
    row = post_migration_db.execute("""
        SELECT
            (SELECT COUNT(*) FROM workflow_sequence_items WHERE result_doc_id IS NOT NULL) AS filled_items,
            (SELECT COUNT(DISTINCT item_id) FROM workflow_item_results WHERE registered_doc_id IS NOT NULL) AS wir_distinct_items
    """).fetchone()
    assert row["filled_items"] >= row["wir_distinct_items"], (
        f"filled_items ({row['filled_items']}) < wir_distinct_items ({row['wir_distinct_items']})"
    )


# ── Invariant tests (DB004 §5) ───────────────────────────────────────────────

def test_invariant_i1_no_orphan_result_doc(post_migration_db):
    """I-1: the doc_id referenced by an item with result_doc_id set must exist in documents."""
    rows = post_migration_db.execute("""
        SELECT id FROM workflow_sequence_items
        WHERE result_doc_id IS NOT NULL
          AND result_doc_id NOT IN (SELECT doc_id FROM documents)
    """).fetchall()
    assert len(rows) == 0, f"orphan result_doc_id rows {len(rows)}: {[r['id'] for r in rows]}"


def test_invariant_i4_no_duplicate_result_doc(post_migration_db):
    """I-4: the same result_doc_id must not be registered in multiple rows."""
    rows = post_migration_db.execute("""
        SELECT result_doc_id, COUNT(*) AS cnt
        FROM workflow_sequence_items
        WHERE result_doc_id IS NOT NULL
        GROUP BY result_doc_id
        HAVING COUNT(*) > 1
    """).fetchall()
    assert len(rows) == 0, f"duplicate result_doc_id: {[dict(r) for r in rows]}"


# ── FK ON DELETE SET NULL test ───────────────────────────────────────────────

def test_fk_on_delete_set_null(post_migration_db):
    """When the document is deleted, result_doc_id must be set to NULL (ON DELETE SET NULL)."""
    conn = post_migration_db
    # Item id=1 references NR001
    before = conn.execute(
        "SELECT result_doc_id FROM workflow_sequence_items WHERE id = 1"
    ).fetchone()
    assert before["result_doc_id"] == "NR001"

    conn.execute("DELETE FROM documents WHERE doc_id = 'NR001'")
    conn.commit()

    after = conn.execute(
        "SELECT result_doc_id FROM workflow_sequence_items WHERE id = 1"
    ).fetchone()
    assert after["result_doc_id"] is None, (
        f"ON DELETE SET NULL did not work: result_doc_id = {after['result_doc_id']}"
    )


# ── Idempotence test ──────────────────────────────────────────────────────────

def test_migration_idempotent_backfill(post_migration_db):
    """Re-running the backfill UPDATE must complete without error (idempotence)."""
    conn = post_migration_db
    conn.execute("""
        UPDATE workflow_sequence_items
        SET result_doc_id = (
            SELECT wir.registered_doc_id
            FROM workflow_item_results AS wir
            WHERE wir.item_id = workflow_sequence_items.id
              AND wir.registered_doc_id IS NOT NULL
            ORDER BY wir.id DESC
            LIMIT 1
        )
        WHERE result_doc_id IS NULL
          AND EXISTS (
            SELECT 1 FROM workflow_item_results wir2
            WHERE wir2.item_id = workflow_sequence_items.id
              AND wir2.registered_doc_id IS NOT NULL
        )
    """)
    conn.commit()
    # no exception = idempotence confirmed


def test_pending_head_by_group_picks_first_unstarted_item(post_migration_db):
    """Regression guard for get_pending_head_by_group ordering."""
    from pathlib import Path
    import json

    queries_path = Path(__file__).resolve().parents[1] / "sql" / "queries" / "queries.json"
    with queries_path.open(encoding="utf-8") as f:
        queries = json.load(f)
    pending_head_sql = queries["workflow_sequence_items"]["get_pending_head_by_group"]

    seed_row = post_migration_db.execute("""
        SELECT d.group_id, d.project_id, ws.id AS seq_id
        FROM workflow_sequence_items wsi
        JOIN workflow_sequences ws ON wsi.sequence_id = ws.id
        JOIN documents d ON ws.doc_id = d.doc_id
        GROUP BY d.group_id, d.project_id, ws.id
        HAVING SUM(CASE WHEN wsi.result_doc_id IS NOT NULL THEN 1 ELSE 0 END) >= 1
           AND SUM(CASE WHEN wsi.result_doc_id IS NULL THEN 1 ELSE 0 END) >= 1
        ORDER BY ws.id
        LIMIT 1
    """).fetchone()
    assert seed_row is not None, "fixture DB needs a mixed workflow sequence for this guard"

    row = post_migration_db.execute(
        pending_head_sql,
        (seed_row["group_id"], seed_row["project_id"]),
    ).fetchone()

    assert row is not None
    assert row["result_doc_id"] is None
    min_pending_sort_order = post_migration_db.execute("""
        SELECT MIN(wsi.sort_order) AS min_sort_order
        FROM workflow_sequence_items wsi
        JOIN workflow_sequences ws ON wsi.sequence_id = ws.id
        JOIN documents d ON ws.doc_id = d.doc_id
        WHERE d.group_id = ? AND d.project_id = ? AND wsi.result_doc_id IS NULL
    """, (seed_row["group_id"], seed_row["project_id"])).fetchone()["min_sort_order"]
    assert row["sort_order"] == min_pending_sort_order

