BEGIN;
-- 0116: preserve provider identity/history, erase connection data on deletion.
-- Nullable secrets/command/URL already exist in 059 in all supported dialects.
-- Applied once by the filename-keyed migration ledger (SQLite/MySQL ADD COLUMN).
ALTER TABLE ai_providers ADD COLUMN IF NOT EXISTS deleted_at TEXT;
DROP INDEX IF EXISTS ux_ai_providers_system_name;
DROP INDEX IF EXISTS ux_ai_providers_user_name;
CREATE UNIQUE INDEX ux_ai_providers_system_name
    ON ai_providers(name) WHERE owner_scope = 'system' AND deleted_at IS NULL;
CREATE UNIQUE INDEX ux_ai_providers_user_name
    ON ai_providers(owner_user_id, name) WHERE owner_scope = 'user' AND deleted_at IS NULL;
COMMIT;
