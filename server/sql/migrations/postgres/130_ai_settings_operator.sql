-- 130_ai_settings_operator.sql
-- flowgate.v02.0017 TR0003: the 09-ai-settings.html elements on top of 129.
-- Same meaning as sqlite/130: team ON/OFF + one default team per project (default_key UNIQUE),
-- role criteria for member fit, reusable evaluation sets, optional human review.

BEGIN;

ALTER TABLE ai_groups ADD COLUMN IF NOT EXISTS enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1));
ALTER TABLE ai_groups ADD COLUMN IF NOT EXISTS default_key TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS uq_ai_groups_default_key ON ai_groups(default_key);

ALTER TABLE ai_group_roles ADD COLUMN IF NOT EXISTS criteria TEXT;

CREATE TABLE IF NOT EXISTS ai_evaluation_sets (
    set_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    name_key TEXT NOT NULL,
    name_key_hash CHAR(64) NOT NULL CHECK (length(name_key_hash) = 64),
    instruction TEXT NOT NULL,
    constraints TEXT,
    created_by TEXT REFERENCES users(user_id) ON DELETE SET NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (project_id, name_key_hash)
);

ALTER TABLE ai_evaluations ADD COLUMN IF NOT EXISTS evaluation_set_id TEXT;
ALTER TABLE ai_evaluations ADD COLUMN IF NOT EXISTS evaluation_set_name TEXT;
ALTER TABLE ai_evaluations ADD COLUMN IF NOT EXISTS human_score INTEGER CHECK (human_score IS NULL OR (human_score >= 0 AND human_score <= 100));
ALTER TABLE ai_evaluations ADD COLUMN IF NOT EXISTS human_comment TEXT;
ALTER TABLE ai_evaluations ADD COLUMN IF NOT EXISTS human_reviewer_id TEXT;
ALTER TABLE ai_evaluations ADD COLUMN IF NOT EXISTS human_reviewed_at TEXT;

COMMIT;
