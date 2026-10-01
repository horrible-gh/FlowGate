-- 129_ai_settings_semantics.sql
-- flowgate.v02.0017 T0002: AI Settings product semantics. Same meaning as the sqlite/postgres
-- files (see sqlite/129 for the full rationale):
--   AI Group = members + user-defined roles + many-to-many mapping + optional Supervisor.
--   The legacy work_executor/reviewer/reworker columns become nullable and are no longer
--   written. Their values move to ai_group_runtime_bindings (runtime slot layer) and every
--   distinct legacy provider becomes a member. No role is invented from the legacy slots.
--   AI Evaluation = judge-scored Scratch task (ai_evaluations + ai_evaluation_scores).
--   ai_evaluation_runs (124) is untouched and read only as AI connection test history.
--
-- MySQL-only, the same premise 086/087/088/123 write down: no IF NOT EXISTS on ADD COLUMN /
-- CREATE INDEX. Re-application safety rests on the migrations ledger keying on the filename.

ALTER TABLE ai_groups MODIFY COLUMN work_executor_provider_id VARCHAR(191) NULL;
ALTER TABLE ai_groups ADD COLUMN supervisor_member_id VARCHAR(191) NULL;

CREATE TABLE ai_group_members (
    member_id VARCHAR(191) PRIMARY KEY,
    ai_group_id VARCHAR(191) NOT NULL,
    provider_id VARCHAR(191) NOT NULL,
    created_at VARCHAR(64) NOT NULL,
    CONSTRAINT fk_ai_group_members_group FOREIGN KEY (ai_group_id) REFERENCES ai_groups(ai_group_id) ON DELETE CASCADE,
    UNIQUE KEY uq_ai_group_members_provider (ai_group_id, provider_id)
);

CREATE TABLE ai_group_roles (
    role_id VARCHAR(191) PRIMARY KEY,
    ai_group_id VARCHAR(191) NOT NULL,
    name VARCHAR(800) NOT NULL,
    name_key VARCHAR(1024) NOT NULL,
    name_key_hash CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL CHECK (CHAR_LENGTH(name_key_hash) = 64),
    description TEXT,
    position INTEGER NOT NULL DEFAULT 0,
    created_at VARCHAR(64) NOT NULL,
    updated_at VARCHAR(64) NOT NULL,
    CONSTRAINT fk_ai_group_roles_group FOREIGN KEY (ai_group_id) REFERENCES ai_groups(ai_group_id) ON DELETE CASCADE,
    UNIQUE KEY uq_ai_group_roles_name_hash (ai_group_id, name_key_hash)
);

CREATE TABLE ai_group_member_roles (
    member_id VARCHAR(191) NOT NULL,
    role_id VARCHAR(191) NOT NULL,
    ai_group_id VARCHAR(191) NOT NULL,
    created_at VARCHAR(64) NOT NULL,
    PRIMARY KEY (member_id, role_id),
    CONSTRAINT fk_ai_group_member_roles_member FOREIGN KEY (member_id) REFERENCES ai_group_members(member_id) ON DELETE CASCADE,
    CONSTRAINT fk_ai_group_member_roles_role FOREIGN KEY (role_id) REFERENCES ai_group_roles(role_id) ON DELETE CASCADE,
    CONSTRAINT fk_ai_group_member_roles_group FOREIGN KEY (ai_group_id) REFERENCES ai_groups(ai_group_id) ON DELETE CASCADE,
    KEY idx_ai_group_member_roles_group (ai_group_id)
);

CREATE TABLE ai_group_runtime_bindings (
    ai_group_id VARCHAR(191) NOT NULL,
    slot VARCHAR(32) NOT NULL CHECK (slot IN ('work_executor', 'reviewer', 'reworker')),
    provider_id VARCHAR(191) NOT NULL,
    updated_at VARCHAR(64) NOT NULL,
    PRIMARY KEY (ai_group_id, slot),
    CONSTRAINT fk_ai_group_runtime_bindings_group FOREIGN KEY (ai_group_id) REFERENCES ai_groups(ai_group_id) ON DELETE CASCADE
);

INSERT INTO ai_group_members (member_id, ai_group_id, provider_id, created_at)
SELECT CONCAT('aigm_', REPLACE(UUID(), '-', '')), legacy.ai_group_id, legacy.provider_id, MIN(legacy.created_at)
FROM (
    SELECT ai_group_id, work_executor_provider_id AS provider_id, created_at FROM ai_groups
        WHERE work_executor_provider_id IS NOT NULL
    UNION
    SELECT ai_group_id, reviewer_provider_id, created_at FROM ai_groups
        WHERE reviewer_provider_id IS NOT NULL
    UNION
    SELECT ai_group_id, reworker_provider_id, created_at FROM ai_groups
        WHERE reworker_provider_id IS NOT NULL
) AS legacy
GROUP BY legacy.ai_group_id, legacy.provider_id;

INSERT INTO ai_group_runtime_bindings (ai_group_id, slot, provider_id, updated_at)
SELECT ai_group_id, 'work_executor', work_executor_provider_id, updated_at FROM ai_groups
    WHERE work_executor_provider_id IS NOT NULL;
INSERT INTO ai_group_runtime_bindings (ai_group_id, slot, provider_id, updated_at)
SELECT ai_group_id, 'reviewer', reviewer_provider_id, updated_at FROM ai_groups
    WHERE reviewer_provider_id IS NOT NULL;
INSERT INTO ai_group_runtime_bindings (ai_group_id, slot, provider_id, updated_at)
SELECT ai_group_id, 'reworker', reworker_provider_id, updated_at FROM ai_groups
    WHERE reworker_provider_id IS NOT NULL;

CREATE TABLE ai_evaluations (
    evaluation_id VARCHAR(191) PRIMARY KEY,
    project_id VARCHAR(191) NOT NULL,
    target_provider_id VARCHAR(191) NOT NULL,
    target_provider_name TEXT,
    judge_provider_id VARCHAR(191) NOT NULL,
    judge_provider_name TEXT,
    lifecycle VARCHAR(16) NOT NULL CHECK (lifecycle IN
        ('pending', 'preparing', 'running', 'reviewing', 'completed', 'failed', 'cancelled')),
    active_dedupe_key CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NULL CHECK (active_dedupe_key IS NULL OR CHAR_LENGTH(active_dedupe_key) = 64),
    task_title TEXT,
    task_summary TEXT,
    task_instruction MEDIUMTEXT,
    target_report MEDIUMTEXT,
    artifacts MEDIUMTEXT,
    scratch_ref VARCHAR(191),
    total_score INTEGER NULL CHECK (total_score IS NULL OR (total_score >= 0 AND total_score <= 100)),
    review_summary MEDIUMTEXT,
    failure_stage VARCHAR(32),
    reason_code VARCHAR(64),
    diagnostic_summary TEXT,
    created_by VARCHAR(191),
    created_at VARCHAR(64) NOT NULL,
    started_at VARCHAR(64),
    finished_at VARCHAR(64),
    updated_at VARCHAR(64) NOT NULL,
    CONSTRAINT chk_ai_evaluations_distinct_judge CHECK (target_provider_id <> judge_provider_id),
    CONSTRAINT fk_ai_evaluations_project FOREIGN KEY (project_id) REFERENCES projects(project_id) ON DELETE CASCADE,
    CONSTRAINT fk_ai_evaluations_creator FOREIGN KEY (created_by) REFERENCES users(user_id) ON DELETE SET NULL,
    UNIQUE KEY uq_ai_evaluations_active (active_dedupe_key),
    KEY idx_ai_evaluations_project_created (project_id, created_at, evaluation_id),
    KEY idx_ai_evaluations_target (project_id, target_provider_id, created_at)
);

CREATE TABLE ai_evaluation_scores (
    evaluation_id VARCHAR(191) NOT NULL,
    criterion_key VARCHAR(64) NOT NULL,
    position INTEGER NOT NULL,
    criterion_description TEXT,
    score INTEGER NULL CHECK (score IS NULL OR (score >= 0 AND score <= 100)),
    rationale TEXT,
    PRIMARY KEY (evaluation_id, criterion_key),
    CONSTRAINT fk_ai_evaluation_scores_evaluation FOREIGN KEY (evaluation_id) REFERENCES ai_evaluations(evaluation_id) ON DELETE CASCADE
);
