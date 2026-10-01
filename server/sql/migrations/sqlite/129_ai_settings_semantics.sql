-- 129_ai_settings_semantics.sql
-- flowgate.v02.0017 T0002: AI Settings product semantics.
--
-- AI Group becomes a team (members + user-defined roles + many-to-many mapping, optional
-- Supervisor). The fixed work_executor / reviewer / reworker trio is no longer the group
-- definition: it moves to ai_group_runtime_bindings, a separate runtime-slot layer that only
-- pre-fills the Start dialog. ai_invoke_runs.requested_ai_group_id stays provenance only.
--
--   ai_groups                  the three legacy provider columns stay (nullable, no longer
--                              written) so nothing already stored is lost. supervisor_member_id
--                              is new. An empty group (no members, no roles) is valid.
--   ai_group_members           one row per (group, AI connection). No FK to the provider
--                              registry: a removed/disabled connection stays visible as broken.
--   ai_group_roles             user-defined roles, unique per group by normalized name hash.
--   ai_group_member_roles      member <-> role, many-to-many.
--   ai_group_runtime_bindings  concrete runtime slots (work_executor/reviewer/reworker).
--
-- Existing groups are carried over: every distinct legacy provider becomes a member and each
-- legacy slot becomes a runtime binding. No role is invented from the legacy slots.
--
-- AI Evaluation becomes a judge-scored Scratch task (ai_evaluations + ai_evaluation_scores).
-- ai_evaluation_runs (124) is left untouched: its suitable/unsuitable baseline rows are now read
-- only as AI connection test history, never as a capability evaluation.
--
-- SQLite cannot drop a NOT NULL in place, so ai_groups is recreated the 123 way: a NEW table is
-- filled, the old one dropped and the new one renamed. Nothing references ai_groups by FK before
-- this file, and the child tables below are created after the swap.

PRAGMA foreign_keys = OFF;
BEGIN;

CREATE TABLE ai_groups__new (
    ai_group_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    name_key TEXT NOT NULL,
    name_key_hash TEXT NOT NULL CHECK (length(name_key_hash) = 64),
    description TEXT,
    work_executor_provider_id TEXT,
    reviewer_provider_id TEXT,
    reworker_provider_id TEXT,
    supervisor_member_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (project_id, name_key_hash)
);

INSERT INTO ai_groups__new
    (ai_group_id, project_id, name, name_key, name_key_hash, description,
     work_executor_provider_id, reviewer_provider_id, reworker_provider_id,
     supervisor_member_id, created_at, updated_at)
SELECT ai_group_id, project_id, name, name_key, name_key_hash, description,
       work_executor_provider_id, reviewer_provider_id, reworker_provider_id,
       NULL, created_at, updated_at
FROM ai_groups;

DROP TABLE ai_groups;
ALTER TABLE ai_groups__new RENAME TO ai_groups;
CREATE INDEX IF NOT EXISTS idx_ai_groups_project_updated ON ai_groups(project_id, updated_at, ai_group_id);

CREATE TABLE ai_group_members (
    member_id TEXT PRIMARY KEY,
    ai_group_id TEXT NOT NULL REFERENCES ai_groups(ai_group_id) ON DELETE CASCADE,
    provider_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (ai_group_id, provider_id)
);

CREATE TABLE ai_group_roles (
    role_id TEXT PRIMARY KEY,
    ai_group_id TEXT NOT NULL REFERENCES ai_groups(ai_group_id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    name_key TEXT NOT NULL,
    name_key_hash TEXT NOT NULL CHECK (length(name_key_hash) = 64),
    description TEXT,
    position INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (ai_group_id, name_key_hash)
);

CREATE TABLE ai_group_member_roles (
    member_id TEXT NOT NULL REFERENCES ai_group_members(member_id) ON DELETE CASCADE,
    role_id TEXT NOT NULL REFERENCES ai_group_roles(role_id) ON DELETE CASCADE,
    ai_group_id TEXT NOT NULL REFERENCES ai_groups(ai_group_id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    PRIMARY KEY (member_id, role_id)
);
CREATE INDEX IF NOT EXISTS idx_ai_group_member_roles_group ON ai_group_member_roles(ai_group_id);

CREATE TABLE ai_group_runtime_bindings (
    ai_group_id TEXT NOT NULL REFERENCES ai_groups(ai_group_id) ON DELETE CASCADE,
    slot TEXT NOT NULL CHECK (slot IN ('work_executor', 'reviewer', 'reworker')),
    provider_id TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (ai_group_id, slot)
);

INSERT INTO ai_group_members (member_id, ai_group_id, provider_id, created_at)
SELECT 'aigm_' || lower(hex(randomblob(16))), legacy.ai_group_id, legacy.provider_id, MIN(legacy.created_at)
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
    evaluation_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
    target_provider_id TEXT NOT NULL,
    target_provider_name TEXT,
    judge_provider_id TEXT NOT NULL,
    judge_provider_name TEXT,
    lifecycle TEXT NOT NULL CHECK (lifecycle IN
        ('pending', 'preparing', 'running', 'reviewing', 'completed', 'failed', 'cancelled')),
    active_dedupe_key TEXT UNIQUE CHECK (active_dedupe_key IS NULL OR length(active_dedupe_key) = 64),
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

CREATE TABLE ai_evaluation_scores (
    evaluation_id TEXT NOT NULL REFERENCES ai_evaluations(evaluation_id) ON DELETE CASCADE,
    criterion_key TEXT NOT NULL,
    position INTEGER NOT NULL,
    criterion_description TEXT,
    score INTEGER CHECK (score IS NULL OR (score >= 0 AND score <= 100)),
    rationale TEXT,
    PRIMARY KEY (evaluation_id, criterion_key)
);

COMMIT;
PRAGMA foreign_keys = ON;
