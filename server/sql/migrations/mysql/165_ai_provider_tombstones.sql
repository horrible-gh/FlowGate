-- 0116: preserve provider identity/history, erase connection data on deletion.
-- Nullable secrets/command/URL already exist in 059 in all supported dialects.
-- Applied once by the filename-keyed migration ledger (SQLite/MySQL ADD COLUMN).
ALTER TABLE ai_providers ADD COLUMN deleted_at VARCHAR(64);
-- MySQL's 131 migration deliberately has no unique name index (TEXT names).
-- Owner-scoped uniqueness remains enforced by the settings service on live rows.
