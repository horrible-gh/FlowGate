-- 135_ai_project_split.sql
-- flowgate.v02.0017 CH0006 (4): AI evaluation and AI groups leave the project, and the project AI
-- policy is retired. AI 관리 is one screen that needs no project.
--
--   ai_evaluations / ai_evaluation_sets / ai_groups  project_id is dropped. Names are unique
--                                over the whole list; a name that now collides with an older
--                                row gets ' · <former project id>' appended.
--   ai_groups.default_key        'default' on the one default team, NULL otherwise. When several
--                                projects had a default team, the most recently updated keeps it.
--   project_ai_policies / project_ai_policy_providers  dropped. Every system AI is usable
--                                everywhere; ai_providers.project_access is no longer read.
--
-- SQLite cannot drop a column that sits in a UNIQUE/FK, so the three tables are recreated the
-- 129 way (new table, copy, drop, rename) with foreign keys off; the child tables keep pointing
-- at the same table names. SQLite has no SHA-256, so a renamed duplicate gets a random
-- name_key_hash; the next rename of that row writes the real one.

PRAGMA foreign_keys = OFF;
BEGIN;

-- Duplicates first, while project_id still tells the rows apart. The oldest row keeps the name.
UPDATE ai_groups
SET name = name || ' · ' || project_id,
    name_key = name_key || ' · ' || lower(project_id),
    name_key_hash = lower(hex(randomblob(32)))
WHERE EXISTS (SELECT 1 FROM ai_groups o
              WHERE o.name_key_hash = ai_groups.name_key_hash
                AND (o.created_at < ai_groups.created_at
                     OR (o.created_at = ai_groups.created_at AND o.ai_group_id < ai_groups.ai_group_id)));

UPDATE ai_evaluation_sets
SET name = name || ' · ' || project_id,
    name_key = name_key || ' · ' || lower(project_id),
    name_key_hash = lower(hex(randomblob(32)))
WHERE EXISTS (SELECT 1 FROM ai_evaluation_sets o
              WHERE o.name_key_hash = ai_evaluation_sets.name_key_hash
                AND (o.created_at < ai_evaluation_sets.created_at
                     OR (o.created_at = ai_evaluation_sets.created_at AND o.set_id < ai_evaluation_sets.set_id)));

-- ai_groups
CREATE TABLE ai_groups__new (
    ai_group_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    name_key TEXT NOT NULL,
    name_key_hash TEXT NOT NULL UNIQUE CHECK (length(name_key_hash) = 64),
    description TEXT,
    work_executor_provider_id TEXT,
    reviewer_provider_id TEXT,
    reworker_provider_id TEXT,
    supervisor_member_id TEXT,
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    default_key TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

INSERT INTO ai_groups__new
    (ai_group_id, name, name_key, name_key_hash, description,
     work_executor_provider_id, reviewer_provider_id, reworker_provider_id,
     supervisor_member_id, enabled, default_key, created_at, updated_at)
SELECT ai_group_id, name, name_key, name_key_hash, description,
       work_executor_provider_id, reviewer_provider_id, reworker_provider_id,
       supervisor_member_id, enabled,
       CASE WHEN ai_group_id = (SELECT d.ai_group_id FROM ai_groups d WHERE d.default_key IS NOT NULL
                                ORDER BY d.updated_at DESC, d.ai_group_id DESC LIMIT 1)
            THEN 'default' END,
       created_at, updated_at
FROM ai_groups;

DROP TABLE ai_groups;
ALTER TABLE ai_groups__new RENAME TO ai_groups;
CREATE INDEX IF NOT EXISTS idx_ai_groups_updated ON ai_groups(updated_at, ai_group_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_ai_groups_default_key ON ai_groups(default_key);

-- ai_evaluation_sets
CREATE TABLE ai_evaluation_sets__new (
    set_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    name_key TEXT NOT NULL,
    name_key_hash TEXT NOT NULL UNIQUE CHECK (length(name_key_hash) = 64),
    instruction TEXT NOT NULL,
    constraints TEXT,
    created_by TEXT REFERENCES users(user_id) ON DELETE SET NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

INSERT INTO ai_evaluation_sets__new
    (set_id, name, name_key, name_key_hash, instruction, constraints, created_by, created_at, updated_at)
SELECT set_id, name, name_key, name_key_hash, instruction, constraints, created_by, created_at, updated_at
FROM ai_evaluation_sets;

DROP TABLE ai_evaluation_sets;
ALTER TABLE ai_evaluation_sets__new RENAME TO ai_evaluation_sets;

-- ai_evaluations
CREATE TABLE ai_evaluations__new (
    evaluation_id TEXT PRIMARY KEY,
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
    evaluation_set_id TEXT,
    evaluation_set_name TEXT,
    human_score INTEGER CHECK (human_score IS NULL OR (human_score >= 0 AND human_score <= 100)),
    human_comment TEXT,
    human_reviewer_id TEXT,
    human_reviewed_at TEXT,
    CHECK (target_provider_id <> judge_provider_id)
);

-- The dedupe key was per project; it is recomputed per (target, judge) by the next start. An
-- evaluation still active here was cut off by the deploy restart and recover_orphans() fails it
-- at boot, so only finished rows matter and none of them holds a key.
INSERT INTO ai_evaluations__new
    (evaluation_id, target_provider_id, target_provider_name, judge_provider_id, judge_provider_name,
     lifecycle, active_dedupe_key, task_title, task_summary, task_instruction, target_report,
     artifacts, scratch_ref, total_score, review_summary, failure_stage, reason_code,
     diagnostic_summary, created_by, created_at, started_at, finished_at, updated_at,
     evaluation_set_id, evaluation_set_name, human_score, human_comment, human_reviewer_id,
     human_reviewed_at)
SELECT evaluation_id, target_provider_id, target_provider_name, judge_provider_id, judge_provider_name,
       lifecycle, NULL, task_title, task_summary, task_instruction, target_report,
       artifacts, scratch_ref, total_score, review_summary, failure_stage, reason_code,
       diagnostic_summary, created_by, created_at, started_at, finished_at, updated_at,
       evaluation_set_id, evaluation_set_name, human_score, human_comment, human_reviewer_id,
       human_reviewed_at
FROM ai_evaluations;

DROP TABLE ai_evaluations;
ALTER TABLE ai_evaluations__new RENAME TO ai_evaluations;
CREATE INDEX IF NOT EXISTS idx_ai_evaluations_created ON ai_evaluations(created_at, evaluation_id);
CREATE INDEX IF NOT EXISTS idx_ai_evaluations_target ON ai_evaluations(target_provider_id, created_at);

-- Project AI policy
DROP TABLE IF EXISTS project_ai_policy_providers;
DROP TABLE IF EXISTS project_ai_policies;

COMMIT;
PRAGMA foreign_keys = ON;
