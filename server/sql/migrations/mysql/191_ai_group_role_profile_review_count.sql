-- 191_ai_group_role_profile_review_count.sql (mysql)
-- flowgate.v02test.0027 (0007-T, A-server): the AI role group's default review count per profile row.
-- See the sqlite file. NULL = inherit (type row) / the existing default 0 (common row); existing rows stay NULL.
-- Applied once by the filename-keyed migration ledger (MySQL has no ADD COLUMN IF NOT EXISTS).
ALTER TABLE ai_group_role_profiles ADD COLUMN review_count INTEGER NULL;

-- ── DOWN (rollback) ──────────────────────────────────────────────────────────
-- ALTER TABLE ai_group_role_profiles DROP COLUMN review_count;
-- ── END DOWN ─────────────────────────────────────────────────────────────────
