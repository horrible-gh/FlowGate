-- 0120 T#1: independent AI role profiles; existing teams stay general.
ALTER TABLE ai_groups ADD COLUMN group_type VARCHAR(16) NOT NULL DEFAULT 'general'
    CHECK (group_type IN ('general', 'role'));
ALTER TABLE ai_groups ADD COLUMN role_group_key VARCHAR(16);
ALTER TABLE ai_groups ADD CONSTRAINT ck_ai_groups_role_type_key
    CHECK ((group_type = 'general' AND role_group_key IS NULL) OR
           (group_type = 'role' AND role_group_key IS NOT NULL AND role_group_key = 'role'));
CREATE UNIQUE INDEX uq_ai_groups_role_group_key ON ai_groups(role_group_key);

CREATE TABLE ai_group_role_profiles (
    ai_group_id VARCHAR(191) NOT NULL,
    type_key VARCHAR(64) NOT NULL,
    position INTEGER NOT NULL CHECK (position >= 0),
    instruction_kind VARCHAR(16) NOT NULL,
    instruction_provider_id VARCHAR(191),
    instruction_provider_name TEXT,
    execution_kind VARCHAR(16) NOT NULL,
    execution_provider_id VARCHAR(191),
    execution_provider_name TEXT,
    review_kind VARCHAR(16) NOT NULL,
    review_provider_id VARCHAR(191),
    review_provider_name TEXT,
    created_at VARCHAR(191) NOT NULL,
    updated_at VARCHAR(191) NOT NULL,
    PRIMARY KEY (ai_group_id, type_key),
    FOREIGN KEY (ai_group_id) REFERENCES ai_groups(ai_group_id) ON DELETE CASCADE,
    CHECK (type_key <> ''),
    CHECK ((type_key = 'common' AND position = 0) OR (type_key <> 'common' AND position > 0)),
    CHECK (((type_key = 'common' AND instruction_kind IN ('unset', 'not_used', 'assigned')) OR
            (type_key <> 'common' AND instruction_kind IN ('inherit', 'not_used', 'assigned'))) AND
           ((instruction_kind = 'assigned' AND instruction_provider_id IS NOT NULL AND instruction_provider_id <> '') OR
            (instruction_kind <> 'assigned' AND instruction_provider_id IS NULL AND instruction_provider_name IS NULL))),
    CHECK (((type_key = 'common' AND execution_kind IN ('unset', 'not_used', 'assigned')) OR
            (type_key <> 'common' AND execution_kind IN ('inherit', 'not_used', 'assigned'))) AND
           ((execution_kind = 'assigned' AND execution_provider_id IS NOT NULL AND execution_provider_id <> '') OR
            (execution_kind <> 'assigned' AND execution_provider_id IS NULL AND execution_provider_name IS NULL))),
    CHECK (((type_key = 'common' AND review_kind IN ('unset', 'not_used', 'assigned')) OR
            (type_key <> 'common' AND review_kind IN ('inherit', 'not_used', 'assigned'))) AND
           ((review_kind = 'assigned' AND review_provider_id IS NOT NULL AND review_provider_id <> '') OR
            (review_kind <> 'assigned' AND review_provider_id IS NULL AND review_provider_name IS NULL)))
);
