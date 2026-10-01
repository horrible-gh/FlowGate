-- 130_ai_settings_operator.sql
-- flowgate.v02.0017 TR0003: the 09-ai-settings.html elements on top of 129.
--
--   ai_groups.enabled            team ON/OFF. An OFF team cannot be picked at Start.
--   ai_groups.default_key        project_id of the project's default team, NULL otherwise.
--                                UNIQUE keeps one default team per project on every dialect.
--   ai_group_roles.criteria      JSON list of evaluation criterion keys the role relies on;
--                                a member's fit is computed from them (NULL = all criteria).
--   ai_evaluation_sets           reusable tasks. An evaluation may run a set instead of a
--                                judge-invented task; the judge still prepares the Scratch.
--   ai_evaluations.human_*       optional human review of a completed evaluation. The human
--                                score, when present, is the evaluation's final score.

PRAGMA foreign_keys = OFF;
BEGIN;

ALTER TABLE ai_groups ADD COLUMN enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1));
ALTER TABLE ai_groups ADD COLUMN default_key TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS uq_ai_groups_default_key ON ai_groups(default_key);

ALTER TABLE ai_group_roles ADD COLUMN criteria TEXT;

CREATE TABLE ai_evaluation_sets (
    set_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    name_key TEXT NOT NULL,
    name_key_hash TEXT NOT NULL CHECK (length(name_key_hash) = 64),
    instruction TEXT NOT NULL,
    constraints TEXT,
    created_by TEXT REFERENCES users(user_id) ON DELETE SET NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (project_id, name_key_hash)
);

ALTER TABLE ai_evaluations ADD COLUMN evaluation_set_id TEXT;
ALTER TABLE ai_evaluations ADD COLUMN evaluation_set_name TEXT;
ALTER TABLE ai_evaluations ADD COLUMN human_score INTEGER CHECK (human_score IS NULL OR (human_score >= 0 AND human_score <= 100));
ALTER TABLE ai_evaluations ADD COLUMN human_comment TEXT;
ALTER TABLE ai_evaluations ADD COLUMN human_reviewer_id TEXT;
ALTER TABLE ai_evaluations ADD COLUMN human_reviewed_at TEXT;

COMMIT;
PRAGMA foreign_keys = ON;
