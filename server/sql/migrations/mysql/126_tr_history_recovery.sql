-- 126_tr_history_recovery.sql
CREATE TABLE IF NOT EXISTS tr_history_recovery (
    id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    recovery_id VARCHAR(64) NOT NULL UNIQUE,
    project_id VARCHAR(255) NOT NULL,
    group_id VARCHAR(255) NOT NULL,
    doc_id VARCHAR(255) NOT NULL,
    operation VARCHAR(32) NOT NULL,
    source_ledger_row_id BIGINT NOT NULL,
    phase VARCHAR(32) NOT NULL,
    pre_git_head_sha VARCHAR(64) NULL,
    git_commit_sha VARCHAR(64) NULL,
    resolution VARCHAR(32) NULL,
    error_detail TEXT NULL,
    created_at VARCHAR(64) NOT NULL,
    updated_at VARCHAR(64) NOT NULL,
    CONSTRAINT chk_tr_history_recovery_operation CHECK (operation IN ('reapply')),
    CONSTRAINT chk_tr_history_recovery_phase CHECK (phase IN ('prepared','git_applied','recovery_required','resolved')),
    CONSTRAINT chk_tr_history_recovery_resolution CHECK (resolution IS NULL OR resolution IN ('ledger_completed','compensated')),
    INDEX idx_tr_history_recovery_group_phase (group_id, phase),
    INDEX idx_tr_history_recovery_source (source_ledger_row_id, id)
);
