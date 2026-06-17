"""
t827_smoke_lg_apply.py
T827 smoke check: apply migration 034 via the LogAssist-backed sqloader runner
to confirm the `near "a": syntax error` is resolved after Path A SQL edit.

Usage:
    python server/tools/t827_smoke_lg_apply.py

Creates a temp SQLite file DB, builds schema (migrations 001–033 via executescript),
recreates documents table without CHECK constraint, seeds T825 fixture rows, then
runs migration 034 strictly through DatabaseMigrator (the same code path that was
failing).  Exits 0 on success, 1 on failure.
"""

import sqlite3
import sys
import tempfile
import os
from pathlib import Path

SCRIPT_DIR     = Path(__file__).resolve().parent
SERVER_DIR     = SCRIPT_DIR.parent
MIGRATIONS_DIR = SERVER_DIR / "sql" / "migrations" / "sqlite"
MIGRATION_034  = MIGRATIONS_DIR / "034_t824_backfill_null_doc_review_status.sql"

# Add server/.venv site-packages to path so sqloader is importable
VENV_SITE = SERVER_DIR / ".venv" / "Lib" / "site-packages"
if str(VENV_SITE) not in sys.path:
    sys.path.insert(0, str(VENV_SITE))

from sqloader.sqlite3 import SQLiteWrapper
from sqloader.migrator import DatabaseMigrator

# ── Fixture (same 9 rows as T825) ─────────────────────────────────────────────
FIXTURE_SQL = """
INSERT INTO documents
    (doc_id, project_id, type_code, seq, title, workflow_steps, doc_review_status,
     created_at, updated_at)
VALUES
    ('row1','p1','NR',1,'Row1 NR null',        NULL,         NULL,             '2025-01-01T00:00:00.000Z','2025-01-01T00:00:00.000Z'),
    ('row2','p1','TR',2,'Row2 TR empty',        '["NR","TR"]','',              '2025-01-01T00:00:00.000Z','2025-01-01T00:00:00.000Z'),
    ('row3','p1','DS',3,'Row3 DS already set',  NULL,         'pending_review','2025-01-01T00:00:00.000Z','2025-01-01T00:00:00.000Z'),
    ('row4','p1','M', 4,'Row4 M approved',      NULL,         'approved',      '2025-01-01T00:00:00.000Z','2025-01-01T00:00:00.000Z'),
    ('row5','p1','M', 5,'Row5 M null',          NULL,         NULL,            '2025-01-01T00:00:00.000Z','2025-01-01T00:00:00.000Z'),
    ('row6','p1','R', 6,'Row6 R undecided',     NULL,         NULL,            '2025-01-01T00:00:00.000Z','2025-01-01T00:00:00.000Z'),
    ('row7','p1','R', 7,'Row7 R decided',       '["DS","D"]', NULL,            '2025-01-01T00:00:00.000Z','2025-01-01T00:00:00.000Z'),
    ('row8','p1','T', 8,'Row8 T rejected',      NULL,         'rejected',      '2025-01-01T00:00:00.000Z','2025-01-01T00:00:00.000Z'),
    ('row9','p1','A', 9,'Row9 A wf_in_progress',NULL,         'wf_in_progress','2025-01-01T00:00:00.000Z','2025-01-01T00:00:00.000Z');
"""

# Recreate documents without CHECK on doc_review_status so empty-string row2 inserts.
RECREATE_SQL = """
PRAGMA foreign_keys = OFF;
DROP VIEW IF EXISTS v_tv_open;
DROP VIEW IF EXISTS v_tv_progress;
CREATE TABLE documents_seed (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id            TEXT    NOT NULL UNIQUE,
    project_id        TEXT    NOT NULL,
    module            TEXT    NOT NULL DEFAULT '__ALL__',
    group_id          TEXT,
    sub_group_id      TEXT,
    type_code         TEXT    NOT NULL,
    seq               INTEGER NOT NULL,
    title             TEXT    NOT NULL,
    file_path         TEXT,
    filename          TEXT,
    status            TEXT    NOT NULL DEFAULT 'draft',
    owner_id          TEXT,
    priority          TEXT,
    due_date          TEXT,
    direction         TEXT,
    review_required   INTEGER NOT NULL DEFAULT 0,
    tv_type           TEXT,
    pass_criteria     TEXT    DEFAULT 'all',
    worker_tier       TEXT,
    target_id         TEXT,
    triggered_by      TEXT,
    superseded_by     TEXT,
    previous_tv       TEXT,
    previous_t        TEXT,
    previous_ds       TEXT,
    created_at        TEXT    NOT NULL,
    meta              TEXT,
    updated_at        TEXT    NOT NULL,
    workflow_steps    TEXT,
    revision_no       INTEGER NOT NULL DEFAULT 0,
    doc_review_status TEXT,
    rejection_reason  TEXT,
    branch            TEXT    NOT NULL DEFAULT 'main',
    rejection_history TEXT    NOT NULL DEFAULT '[]'
);
INSERT INTO documents_seed SELECT * FROM documents;
DROP TABLE documents;
ALTER TABLE documents_seed RENAME TO documents;
CREATE UNIQUE INDEX IF NOT EXISTS ux_documents_doc_id ON documents(doc_id);
CREATE INDEX IF NOT EXISTS idx_documents_prj_mod_grp_status ON documents(project_id, module, group_id, status);
CREATE INDEX IF NOT EXISTS idx_documents_prj_type_status ON documents(project_id, type_code, status);
CREATE INDEX IF NOT EXISTS idx_documents_owner ON documents(owner_id);
CREATE INDEX IF NOT EXISTS idx_documents_updated ON documents(updated_at DESC);
PRAGMA foreign_keys = ON;
"""


def main():
    print("=== T827 Smoke Check: LogAssist-backed runner for migration 034 ===")

    # 1. Confirm migration file exists
    if not MIGRATION_034.exists():
        print(f"ABORT: migration file not found: {MIGRATION_034}")
        sys.exit(1)

    # 2. Create temp DB file
    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".db", prefix="t827_smoke_")
    os.close(tmp_fd)
    print(f"Temp DB: {tmp_path}")

    try:
        # 3. Build schema (001–033) via executescript on a plain connection
        pre_migrations = sorted(
            f for f in MIGRATIONS_DIR.glob("*.sql")
            if f.name < "034_"
        )
        print(f"Applying {len(pre_migrations)} pre-034 migrations via executescript …")
        plain = sqlite3.connect(tmp_path)
        for mf in pre_migrations:
            sql = mf.read_text(encoding="utf-8")
            plain.executescript(sql)
        plain.close()
        print("  Schema built OK.")

        # 4. Recreate documents table without CHECK + seed fixtures
        plain = sqlite3.connect(tmp_path)
        plain.executescript(RECREATE_SQL)
        plain.executescript("PRAGMA foreign_keys = OFF;" + FIXTURE_SQL + "PRAGMA foreign_keys = ON;")
        plain.close()
        print("  Fixture seeded (9 rows).")

        # 5. Run migration 034 via DatabaseMigrator (the LogAssist runner path)
        print("Running DatabaseMigrator.apply_migration('034_t824_backfill_null_doc_review_status.sql') …")
        db = SQLiteWrapper(db_name=tmp_path, memory_mode=False)
        migrator = DatabaseMigrator(db, str(MIGRATIONS_DIR), auto_run=False)

        # Only apply 034; skip anything else already applied
        migrator.apply_migration("034_t824_backfill_null_doc_review_status.sql")

        # 6. Confirm migration recorded
        plain = sqlite3.connect(tmp_path)
        row = plain.execute(
            "SELECT filename, applied_at FROM migrations "
            "WHERE filename = '034_t824_backfill_null_doc_review_status.sql'"
        ).fetchone()
        plain.close()

        if row:
            print(f"  Migration recorded: filename={row[0]!r}  applied_at={row[1]!r}")
        else:
            print("  ERROR: Migration row not found in migrations table after apply!")
            sys.exit(1)

        print()
        print("=== RESULT: PASS — migration 034 applied via LogAssist runner without syntax error ===")

    except Exception as e:
        print(f"\n=== RESULT: FAIL — {e} ===")
        sys.exit(1)
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


if __name__ == "__main__":
    main()
