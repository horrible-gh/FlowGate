-- 191_ai_group_role_profile_review_count.sql (postgres)
-- flowgate.v02test.0027 (0007-T, A-server): the AI role group's default review count per profile row.
-- See the sqlite file. NULL = inherit (type row) / the existing default 0 (common row); existing rows stay NULL.
ALTER TABLE ai_group_role_profiles ADD COLUMN IF NOT EXISTS review_count INTEGER;

-- ── DOWN (rollback) ──────────────────────────────────────────────────────────
-- ALTER TABLE ai_group_role_profiles DROP COLUMN IF EXISTS review_count;
-- ── END DOWN ─────────────────────────────────────────────────────────────────
