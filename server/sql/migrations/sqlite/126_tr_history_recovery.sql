-- 126_tr_history_recovery.sql
-- flowgate.default.0648: durable bridge across Git reapply and ledger finalize.
CREATE TABLE IF NOT EXISTS tr_history_recovery (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    recovery_id TEXT NOT NULL UNIQUE,
    project_id TEXT NOT NULL,
    group_id TEXT NOT NULL,
    doc_id TEXT NOT NULL,
    operation TEXT NOT NULL CHECK (operation IN ('reapply')),
    source_ledger_row_id INTEGER NOT NULL,
    phase TEXT NOT NULL CHECK (phase IN ('prepared','git_applied','recovery_required','resolved')),
    pre_git_head_sha TEXT,
    git_commit_sha TEXT,
    resolution TEXT CHECK (resolution IS NULL OR resolution IN ('ledger_completed','compensated')),
    error_detail TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tr_history_recovery_group_phase
    ON tr_history_recovery(group_id, phase);
CREATE INDEX IF NOT EXISTS idx_tr_history_recovery_source
    ON tr_history_recovery(source_ledger_row_id, id);
