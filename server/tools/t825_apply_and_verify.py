"""
t825_apply_and_verify.py
T825: Apply T824 backfill migration on in-memory SQLite and verify via SQL queries.

Usage:
    python server/tools/t825_apply_and_verify.py

Reads the T824 migration from the filesystem (does NOT inline its content).
Uses sqlite3.connect(":memory:") — no real DB is touched.
"""

import sqlite3
import sys
from pathlib import Path

# ── Paths ─────────────────────────────────────────────────────────────────────
SCRIPT_DIR     = Path(__file__).resolve().parent
SERVER_DIR     = SCRIPT_DIR.parent
MIGRATIONS_DIR = SERVER_DIR / "sql" / "migrations" / "sqlite"
MIGRATION_034  = MIGRATIONS_DIR / "034_t824_backfill_null_doc_review_status.sql"

REPORT_PATH = (
    Path(__file__).resolve().parents[3]
    / "_tools" / "ai_launcher" / "work" / "reports" / "apply_backfill_verify.md"
)

# ── Fixture INSERTs ────────────────────────────────────────────────────────────
# Columns required (NOT NULL, no default): doc_id, project_id, type_code, seq, title,
#   created_at, updated_at.  All others nullable or have defaults.
# We use PRAGMA foreign_keys = OFF when seeding so we don't need real FK rows.
FIXTURE_SQL = """
INSERT INTO documents
    (doc_id, project_id, type_code, seq, title, workflow_steps, doc_review_status,
     created_at, updated_at)
VALUES
    ('row1', 'p1', 'NR', 1, 'Row1 NR null',         NULL,         NULL,             '2025-01-01T00:00:00.000Z', '2025-01-01T00:00:00.000Z'),
    ('row2', 'p1', 'TR', 2, 'Row2 TR empty',         '["NR","TR"]','',               '2025-01-01T00:00:00.000Z', '2025-01-01T00:00:00.000Z'),
    ('row3', 'p1', 'DS', 3, 'Row3 DS already set',   NULL,         'pending_review', '2025-01-01T00:00:00.000Z', '2025-01-01T00:00:00.000Z'),
    ('row4', 'p1', 'M',  4, 'Row4 M approved',       NULL,         'approved',       '2025-01-01T00:00:00.000Z', '2025-01-01T00:00:00.000Z'),
    ('row5', 'p1', 'M',  5, 'Row5 M null',           NULL,         NULL,             '2025-01-01T00:00:00.000Z', '2025-01-01T00:00:00.000Z'),
    ('row6', 'p1', 'R',  6, 'Row6 R undecided',      NULL,         NULL,             '2025-01-01T00:00:00.000Z', '2025-01-01T00:00:00.000Z'),
    ('row7', 'p1', 'R',  7, 'Row7 R decided',        '["DS","D"]', NULL,             '2025-01-01T00:00:00.000Z', '2025-01-01T00:00:00.000Z'),
    ('row8', 'p1', 'T',  8, 'Row8 T rejected',       NULL,         'rejected',       '2025-01-01T00:00:00.000Z', '2025-01-01T00:00:00.000Z'),
    ('row9', 'p1', 'A',  9, 'Row9 A wf_in_progress', NULL,         'wf_in_progress', '2025-01-01T00:00:00.000Z', '2025-01-01T00:00:00.000Z');
"""

FIXTURE_INSERTS_DISPLAY = [
    "('row1', 'p1', 'NR', 1, 'Row1 NR null',         NULL,         NULL,             '2025-01-01T...')",
    "('row2', 'p1', 'TR', 2, 'Row2 TR empty',         '[\"NR\",\"TR\"]', '',          '2025-01-01T...')",
    "('row3', 'p1', 'DS', 3, 'Row3 DS already set',   NULL,         'pending_review', '2025-01-01T...')",
    "('row4', 'p1', 'M',  4, 'Row4 M approved',       NULL,         'approved',       '2025-01-01T...')",
    "('row5', 'p1', 'M',  5, 'Row5 M null',           NULL,         NULL,             '2025-01-01T...')",
    "('row6', 'p1', 'R',  6, 'Row6 R undecided',      NULL,         NULL,             '2025-01-01T...')",
    "('row7', 'p1', 'R',  7, 'Row7 R decided',        '[\"DS\",\"D\"]', NULL,         '2025-01-01T...')",
    "('row8', 'p1', 'T',  8, 'Row8 T rejected',       NULL,         'rejected',       '2025-01-01T...')",
    "('row9', 'p1', 'A',  9, 'Row9 A wf_in_progress', NULL,         'wf_in_progress', '2025-01-01T...')",
]

# ── Expected results ───────────────────────────────────────────────────────────
EXPECTED = {
    "row1": "pending_review",
    "row2": "pending_review",
    "row3": "pending_review",
    "row4": "approved",
    "row5": None,
    "row6": None,
    "row7": "pending_review",
    "row8": "rejected",
    "row9": "wf_in_progress",
}

# ── Helpers ────────────────────────────────────────────────────────────────────

def fmt_val(v):
    return repr(v) if v is not None else "NULL"


def run_q1(cur):
    cur.execute("""
        SELECT doc_id, type_code, workflow_steps, doc_review_status
        FROM documents
        WHERE doc_id IN ('row1','row2','row7')
        ORDER BY doc_id
    """)
    return cur.fetchall()


def run_q2(cur):
    cur.execute("""
        SELECT doc_id, type_code, workflow_steps, doc_review_status
        FROM documents
        WHERE doc_id IN ('row3','row4','row5','row6','row8','row9')
        ORDER BY doc_id
    """)
    return cur.fetchall()


def run_q3(cur):
    cur.execute("""
        SELECT COUNT(*) FROM documents
        WHERE (doc_review_status IS NULL OR doc_review_status = '')
          AND type_code != 'R' AND type_code != 'M'
    """)
    return cur.fetchone()[0]


def rows_to_md(rows):
    lines = ["| doc_id | type_code | workflow_steps | doc_review_status |",
             "|--------|-----------|----------------|-------------------|"]
    for r in rows:
        lines.append(f"| {r[0]} | {r[1]} | {fmt_val(r[2])} | {fmt_val(r[3])} |")
    return "\n".join(lines)


def count_updated(con_before, con_after):
    """Return number of rows where doc_review_status differs between two snapshots."""
    # We snapshot by querying doc_id -> doc_review_status before/after
    # Since it's the same connection, we just re-query after the second run
    pass


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    lines = []  # report lines

    def log(s=""):
        print(s)
        lines.append(s)

    # ── 0. Locate migration 034 ────────────────────────────────────────────────
    log("## T825 — Apply T824 Backfill Migration and Verify")
    log()

    if not MIGRATION_034.exists():
        log(f"**ABORT**: Migration file not found: {MIGRATION_034}")
        log()
        log("**Overall result: FAIL** — T824 migration file missing.")
        write_report("\n".join(lines))
        sys.exit(1)

    migration_sql = MIGRATION_034.read_text(encoding="utf-8")
    migration_size = MIGRATION_034.stat().st_size
    first_comment_lines = [ln for ln in migration_sql.splitlines() if ln.startswith("--")][:8]

    # ── 1. Migration file located ──────────────────────────────────────────────
    log("## 1. Migration File Located")
    log()
    log(f"- **Path:** `{MIGRATION_034}`")
    log(f"- **Size:** {migration_size} bytes")
    log("- **Header comment block:**")
    log()
    log("```")
    for cl in first_comment_lines:
        log(cl)
    log("```")
    log()

    # ── 2. Build in-memory DB (migrations 001–033) ─────────────────────────────
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row

    pre_t824 = sorted(
        f for f in MIGRATIONS_DIR.glob("*.sql")
        if f.name < "034_"
    )

    log("## 2. Fixture Set Used")
    log()
    log(f"Applied **{len(pre_t824)} migration(s)** (001–033) to build schema:")
    log()

    failed_migrations = []
    for mf in pre_t824:
        sql_text = mf.read_text(encoding="utf-8")
        try:
            con.executescript(sql_text)
        except sqlite3.Error as e:
            failed_migrations.append((mf.name, str(e)))

    if failed_migrations:
        log("**ABORT**: The following pre-T824 migrations failed to apply:")
        for name, err in failed_migrations:
            log(f"  - `{name}`: {err}")
        log()
        log("**Overall result: FAIL** — schema setup failed.")
        write_report("\n".join(lines))
        sys.exit(1)

    # Seed fixtures.
    # doc_review_status has a CHECK constraint (migration 024 / 027) that prevents
    # inserting legacy empty-string values through normal SQL.  Row 2 needs
    # doc_review_status = '' to exercise the (IS NULL OR = '') branch of T824.
    # Solution: drop dependent views, recreate documents WITHOUT the CHECK on
    # doc_review_status (simulating a pre-constraint legacy row), insert all
    # fixtures, then apply migration 034.  Migration 034 itself only performs an
    # UPDATE; it does not depend on the CHECK constraint being present.
    recreate_sql = """
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

CREATE UNIQUE INDEX IF NOT EXISTS ux_documents_doc_id
    ON documents(doc_id);
CREATE INDEX IF NOT EXISTS idx_documents_prj_mod_grp_status
    ON documents(project_id, module, group_id, status);
CREATE INDEX IF NOT EXISTS idx_documents_prj_type_status
    ON documents(project_id, type_code, status);
CREATE INDEX IF NOT EXISTS idx_documents_owner
    ON documents(owner_id);
CREATE INDEX IF NOT EXISTS idx_documents_updated
    ON documents(updated_at DESC);

PRAGMA foreign_keys = ON;
"""
    try:
        con.executescript(recreate_sql)
    except sqlite3.Error as e:
        log(f"**ABORT**: Schema preparation for seeding failed: {e}")
        log()
        log("**Overall result: FAIL** — could not prepare documents table for fixtures.")
        write_report("\n".join(lines))
        sys.exit(1)

    try:
        con.executescript("PRAGMA foreign_keys = OFF;" + FIXTURE_SQL + "PRAGMA foreign_keys = ON;")
    except sqlite3.Error as e:
        log(f"**ABORT**: Fixture insert failed: {e}")
        log()
        log("**Overall result: FAIL** — fixture seeding failed.")
        write_report("\n".join(lines))
        sys.exit(1)

    log("**INSERT INTO documents(doc_id, project_id, type_code, seq, title, workflow_steps, doc_review_status, created_at, updated_at) VALUES**")
    log()
    log("```sql")
    for ins in FIXTURE_INSERTS_DISPLAY:
        log(f"    {ins},")
    log("```")
    log()

    # ── 3. Pre-migration state ─────────────────────────────────────────────────
    cur = con.cursor()
    pre_q1 = run_q1(cur)
    pre_q2 = run_q2(cur)
    pre_q3 = run_q3(cur)

    log("## 3. Pre-Migration State")
    log()
    log("**Q1** (rows expected to be backfilled — row1, row2, row7):")
    log()
    log(rows_to_md(pre_q1))
    log()
    log("**Q2** (rows expected to be unchanged — row3–row6, row8, row9):")
    log()
    log(rows_to_md(pre_q2))
    log()
    log(f"**Q3** (NULL/empty non-R, non-M rows before migration): `{pre_q3}`")
    log()

    # ── 4. Apply migration 034 (first run) ─────────────────────────────────────
    try:
        con.executescript(migration_sql)
    except sqlite3.Error as e:
        log(f"**ABORT**: Migration 034 failed to apply: {e}")
        log()
        log("**Overall result: FAIL** — T824 migration SQL error.")
        write_report("\n".join(lines))
        sys.exit(1)

    post_q1 = run_q1(cur)
    post_q2 = run_q2(cur)
    post_q3 = run_q3(cur)

    log("## 4. Post-Migration State (First Run)")
    log()
    log("**Q1** (rows1, row2, row7 — all should be `'pending_review'`):")
    log()
    log(rows_to_md(post_q1))
    log()
    log("**Q2** (row3–row6, row8, row9 — must be unchanged):")
    log()
    log(rows_to_md(post_q2))
    log()
    log(f"**Q3** (NULL/empty non-R, non-M rows post-migration): `{post_q3}`")
    if post_q3 == 0:
        log("  → ✅ Q3 = 0 as expected")
    else:
        log(f"  → ❌ Q3 = {post_q3} — expected 0")
    log()

    # ── 5. Per-row verdict table ───────────────────────────────────────────────
    log("## 5. Per-Row Verdict Table")
    log()
    log("| Row | type_code | workflow_steps | Expected post | Actual post | Verdict |")
    log("|-----|-----------|----------------|---------------|-------------|---------|")

    # Build actual dict
    cur.execute("SELECT doc_id, doc_review_status FROM documents ORDER BY doc_id")
    actual = {r[0]: r[1] for r in cur.fetchall()}

    all_pass = True
    for row_id in sorted(EXPECTED.keys()):
        exp = EXPECTED[row_id]
        act = actual.get(row_id)
        verdict = "✅ PASS" if exp == act else "❌ FAIL"
        if exp != act:
            all_pass = False
        cur.execute("SELECT type_code, workflow_steps FROM documents WHERE doc_id = ?", (row_id,))
        info = cur.fetchone()
        tc = info[0] if info else "?"
        wf = fmt_val(info[1]) if info else "?"
        log(f"| {row_id} | {tc} | {wf} | {fmt_val(exp)} | {fmt_val(act)} | {verdict} |")

    log()

    # ── 6. Idempotency (second run) ────────────────────────────────────────────
    # Snapshot doc_review_status before second run
    cur.execute("SELECT doc_id, doc_review_status FROM documents ORDER BY doc_id")
    before_second = {r[0]: r[1] for r in cur.fetchall()}

    con.executescript(migration_sql)

    cur.execute("SELECT doc_id, doc_review_status FROM documents ORDER BY doc_id")
    after_second = {r[0]: r[1] for r in cur.fetchall()}

    changed_on_second = [
        k for k in before_second if before_second[k] != after_second[k]
    ]

    log("## 6. Idempotency (Second Run)")
    log()
    if not changed_on_second:
        log("Re-applied migration 034 a second time. **Zero rows changed.** ✅")
    else:
        log(f"Re-applied migration 034. **{len(changed_on_second)} row(s) changed** on second run — idempotency FAILED:")
        for k in changed_on_second:
            log(f"  - {k}: {fmt_val(before_second[k])} → {fmt_val(after_second[k])}")
        all_pass = False
    log()

    idempotency_pass = len(changed_on_second) == 0

    # ── 7. Overall result ──────────────────────────────────────────────────────
    log("## 7. Overall Result")
    log()
    if all_pass and idempotency_pass and post_q3 == 0:
        log("**Overall result: PASS** — all 9 rows match expectations and idempotency holds.")
    else:
        fails = []
        for row_id in sorted(EXPECTED.keys()):
            if EXPECTED[row_id] != actual.get(row_id):
                fails.append(f"{row_id}: expected {fmt_val(EXPECTED[row_id])}, got {fmt_val(actual.get(row_id))}")
        if not idempotency_pass:
            fails.append("idempotency: rows changed on second run")
        if post_q3 != 0:
            fails.append(f"Q3: {post_q3} non-R/M rows still have NULL/empty status after migration")
        log("**Overall result: FAIL**")
        log()
        for f in fails:
            log(f"  - {f}")
    log()

    # ── 8. Script path + invocation ───────────────────────────────────────────
    log("## 8. Script Path + Invocation")
    log()
    log(f"- **Script:** `{Path(__file__).resolve()}`")
    log("- **Run from FlowGate repo root:**")
    log()
    log("```bash")
    log("python server/tools/t825_apply_and_verify.py")
    log("```")
    log()

    # Write report
    write_report("\n".join(lines))
    print()
    print(f"Report written to: {REPORT_PATH}")

    if not (all_pass and idempotency_pass and post_q3 == 0):
        sys.exit(1)


def write_report(content: str):
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(content, encoding="utf-8")


if __name__ == "__main__":
    main()
