-- 191_ai_group_role_profile_review_count.sql (sqlite)
-- flowgate.v02test.0027 (0007-T, A-server): the AI role group's default review count per profile row.
--   ai_group_role_profiles.review_count  NULL on a document-type row = inherit the common row;
--                                        NULL on the common row = the existing card default (0, no review);
--                                        0 = explicitly no review; 1..N / -1 as the card's 검수 횟수.
-- Copied once into a new card's settings when the card is created (flowgate.ai_roles.card_init); never read at run
-- time and never applied to existing cards. Existing rows keep NULL (= today's behaviour). The allowed values are the
-- server policy's (ai_execution_policy_service.repeat_count_choices), so no CHECK pins a fixed range here.
-- Applied once by the filename-keyed migration ledger (SQLite has no ADD COLUMN IF NOT EXISTS).
ALTER TABLE ai_group_role_profiles ADD COLUMN review_count INTEGER;

-- ── DOWN (rollback) ──────────────────────────────────────────────────────────
-- ALTER TABLE ai_group_role_profiles DROP COLUMN review_count;
-- ── END DOWN ─────────────────────────────────────────────────────────────────
