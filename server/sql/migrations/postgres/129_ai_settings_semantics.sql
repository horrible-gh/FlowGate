-- 129_ai_settings_semantics.sql
-- flowgate.v02.0017 T0002: AI Settings product semantics. Same meaning as the sqlite/mysql
-- files (see sqlite/129 for the full rationale):
--   AI Group = members + user-defined roles + many-to-many mapping + optional Supervisor.
--   The legacy work_executor/reviewer/reworker columns become nullable and are no longer
--   written. Their values move to ai_group_runtime_bindings (runtime slot layer) and every
--   distinct legacy provider becomes a member. No role is invented from the legacy slots.
--   AI Evaluation = judge-scored Scratch task (ai_evaluations + ai_evaluation_scores).
--   ai_evaluation_runs (124) is untouched and read only as AI connection test history.
-- Postgres drops a NOT NULL in place. IF NOT EXISTS follows the 086/088/123 postgres-only note.

BEGIN;

ALTER TABLE ai_groups ALTER COLUMN work_executor_provider_id DROP NOT NULL;
ALTER TABLE ai_groups ADD COLUMN IF NOT EXISTS supervisor_member_id TEXT;

CREATE TABLE IF NOT EXISTS ai_group_members (
    member_id TEXT PRIMARY KEY,
    ai_group_id TEXT NOT NULL REFERENCES ai_groups(ai_group_id) ON DELETE CASCADE,
    provider_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (ai_group_id, provider_id)
);

CREATE TABLE IF NOT EXISTS ai_group_roles (
    role_id TEXT PRIMARY KEY,
    ai_group_id TEXT NOT NULL REFERENCES ai_groups(ai_group_id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    name_key TEXT NOT NULL,
    name_key_hash CHAR(64) NOT NULL CHECK (length(name_key_hash) = 64),
    description TEXT,
    position INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (ai_group_id, name_key_hash)
);

CREATE TABLE IF NOT EXISTS ai_group_member_roles (
    member_id TEXT NOT NULL REFERENCES ai_group_members(member_id) ON DELETE CASCADE,
    role_id TEXT NOT NULL REFERENCES ai_group_roles(role_id) ON DELETE CASCADE,
    ai_group_id TEXT NOT NULL REFERENCES ai_groups(ai_group_id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    PRIMARY KEY (member_id, role_id)
);
CREATE INDEX IF NOT EXISTS idx_ai_group_member_roles_group ON ai_group_member_roles(ai_group_id);

CREATE TABLE IF NOT EXISTS ai_group_runtime_bindings (
    ai_group_id TEXT NOT NULL REFERENCES ai_groups(ai_group_id) ON DELETE CASCADE,
    slot TEXT NOT NULL CHECK (slot IN ('work_executor', 'reviewer', 'reworker')),
    provider_id TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (ai_group_id, slot)
);

INSERT INTO ai_group_members (member_id, ai_group_id, provider_id, created_at)
SELECT 'aigm_' || md5(legacy.ai_group_id || ':' || legacy.provider_id), legacy.ai_group_id,
       legacy.provider_id, MIN(legacy.created_at)
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
GROUP BY legacy.ai_group_id, legacy.provider_id
ON CONFLICT DO NOTHING;

INSERT INTO ai_group_runtime_bindings (ai_group_id, slot, provider_id, updated_at)
SELECT ai_group_id, 'work_executor', work_executor_provider_id, updated_at FROM ai_groups
    WHERE work_executor_provider_id IS NOT NULL
ON CONFLICT DO NOTHING;
INSERT INTO ai_group_runtime_bindings (ai_group_id, slot, provider_id, updated_at)
SELECT ai_group_id, 'reviewer', reviewer_provider_id, updated_at FROM ai_groups
    WHERE reviewer_provider_id IS NOT NULL
ON CONFLICT DO NOTHING;
INSERT INTO ai_group_runtime_bindings (ai_group_id, slot, provider_id, updated_at)
SELECT ai_group_id, 'reworker', reworker_provider_id, updated_at FROM ai_groups
    WHERE reworker_provider_id IS NOT NULL
ON CONFLICT DO NOTHING;

CREATE TABLE IF NOT EXISTS ai_evaluations (
    evaluation_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
    target_provider_id TEXT NOT NULL,
    target_provider_name TEXT,
    judge_provider_id TEXT NOT NULL,
    judge_provider_name TEXT,
    lifecycle TEXT NOT NULL CHECK (lifecycle IN
        ('pending', 'preparing', 'running', 'reviewing', 'completed', 'failed', 'cancelled')),
    active_dedupe_key CHAR(64) UNIQUE CHECK (active_dedupe_key IS NULL OR length(active_dedupe_key) = 64),
    task_title TEXT,
    task_summary TEXT,
    task_instruction TEXT,
    target_report TEXT,
    artifacts TEXT,
    scratch_ref TEXT,
    total_score INTEGER CHECK (total_score IS NULL OR (total_score >= 0 AND total_score <= 100)),
    review_summary TEXT,
    failure_stage TEXT,
    reason_code TEXT,
    diagnostic_summary TEXT,
    created_by TEXT REFERENCES users(user_id) ON DELETE SET NULL,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    updated_at TEXT NOT NULL,
    CHECK (target_provider_id <> judge_provider_id)
);
CREATE INDEX IF NOT EXISTS idx_ai_evaluations_project_created ON ai_evaluations(project_id, created_at, evaluation_id);
CREATE INDEX IF NOT EXISTS idx_ai_evaluations_target ON ai_evaluations(project_id, target_provider_id, created_at);

CREATE TABLE IF NOT EXISTS ai_evaluation_scores (
    evaluation_id TEXT NOT NULL REFERENCES ai_evaluations(evaluation_id) ON DELETE CASCADE,
    criterion_key TEXT NOT NULL,
    position INTEGER NOT NULL,
    criterion_description TEXT,
    score INTEGER CHECK (score IS NULL OR (score >= 0 AND score <= 100)),
    rationale TEXT,
    PRIMARY KEY (evaluation_id, criterion_key)
);

COMMIT;
