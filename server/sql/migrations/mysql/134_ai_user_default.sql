-- 134_ai_user_default.sql  (mysql)
-- flowgate.v02.0017 CH0006 (3): the default AI is personal. Each user may pick one AI (a system AI
-- or one of their own); with no pick the user follows the system default. A project no longer
-- keeps a default AI, so project_ai_policies.default_provider_id is cleared here and no longer read
-- (the policy table itself leaves with the project split).
--   user_ai_defaults.provider_id  the user's default AI; the row is deleted to follow the system
--                                 default, and SET NULL when the AI is deleted reads the same way.

CREATE TABLE user_ai_defaults (
    user_id VARCHAR(191) NOT NULL PRIMARY KEY,
    provider_id VARCHAR(191) NULL,
    updated_at VARCHAR(64) NOT NULL,
    CONSTRAINT fk_uad_user FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE,
    CONSTRAINT fk_uad_provider FOREIGN KEY (provider_id) REFERENCES ai_providers(provider_id) ON DELETE SET NULL
);

UPDATE project_ai_policies SET default_provider_id = NULL WHERE default_provider_id IS NOT NULL;
