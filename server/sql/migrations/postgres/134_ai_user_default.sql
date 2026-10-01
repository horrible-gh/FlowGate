-- 134_ai_user_default.sql  (postgres)
-- flowgate.v02.0017 CH0006 (3): the default AI is personal. Each user may pick one AI (a system AI
-- or one of their own); with no pick the user follows the system default. A project no longer
-- keeps a default AI, so project_ai_policies.default_provider_id is cleared here and no longer read
-- (the policy table itself leaves with the project split).
--   user_ai_defaults.provider_id  the user's default AI; the row is deleted to follow the system
--                                 default, and SET NULL when the AI is deleted reads the same way.

CREATE TABLE IF NOT EXISTS user_ai_defaults (
    user_id TEXT PRIMARY KEY REFERENCES users(user_id) ON DELETE CASCADE,
    provider_id TEXT REFERENCES ai_providers(provider_id) ON DELETE SET NULL,
    updated_at TEXT NOT NULL
);

UPDATE project_ai_policies SET default_provider_id = NULL WHERE default_provider_id IS NOT NULL;
