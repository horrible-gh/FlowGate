-- 130_ai_settings_operator.sql
-- flowgate.v02.0017 TR0003: the 09-ai-settings.html elements on top of 129.
-- Same meaning as sqlite/130: team ON/OFF + one default team per project (default_key UNIQUE),
-- role criteria for member fit, reusable evaluation sets, optional human review.

ALTER TABLE ai_groups ADD COLUMN enabled TINYINT NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1));
ALTER TABLE ai_groups ADD COLUMN default_key VARCHAR(191) NULL;
ALTER TABLE ai_groups ADD UNIQUE KEY uq_ai_groups_default_key (default_key);

ALTER TABLE ai_group_roles ADD COLUMN criteria TEXT NULL;

CREATE TABLE ai_evaluation_sets (
    set_id VARCHAR(191) PRIMARY KEY,
    project_id VARCHAR(191) NOT NULL,
    name VARCHAR(800) NOT NULL,
    name_key VARCHAR(1024) NOT NULL,
    name_key_hash CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL CHECK (CHAR_LENGTH(name_key_hash) = 64),
    instruction MEDIUMTEXT NOT NULL,
    constraints TEXT,
    created_by VARCHAR(191),
    created_at VARCHAR(64) NOT NULL,
    updated_at VARCHAR(64) NOT NULL,
    CONSTRAINT fk_ai_evaluation_sets_project FOREIGN KEY (project_id) REFERENCES projects(project_id) ON DELETE CASCADE,
    CONSTRAINT fk_ai_evaluation_sets_creator FOREIGN KEY (created_by) REFERENCES users(user_id) ON DELETE SET NULL,
    UNIQUE KEY uq_ai_evaluation_sets_name_hash (project_id, name_key_hash)
);

ALTER TABLE ai_evaluations ADD COLUMN evaluation_set_id VARCHAR(191) NULL;
ALTER TABLE ai_evaluations ADD COLUMN evaluation_set_name TEXT NULL;
ALTER TABLE ai_evaluations ADD COLUMN human_score INTEGER NULL CHECK (human_score IS NULL OR (human_score >= 0 AND human_score <= 100));
ALTER TABLE ai_evaluations ADD COLUMN human_comment MEDIUMTEXT NULL;
ALTER TABLE ai_evaluations ADD COLUMN human_reviewer_id VARCHAR(191) NULL;
ALTER TABLE ai_evaluations ADD COLUMN human_reviewed_at VARCHAR(64) NULL;
