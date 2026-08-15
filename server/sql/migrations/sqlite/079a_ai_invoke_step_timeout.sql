-- 079a_ai_invoke_step_timeout.sql
-- flowgate.default.0400 T0006 (R0001 / M0005): ContinuousWorkDialog's 기본 설정 탭 gained a
-- 시간 section — a per-hop wall-clock budget the user picks (30/45/60/90/120/180/240 minutes),
-- replacing the fixed HOP_TIMEOUT_SEC (always 60 minutes) for chains that need longer steps.
-- Additive, nullable: NULL on a paused row means "no pick was made", and the engine falls back
-- to HOP_TIMEOUT_SEC exactly like it always did — pre-migration rows keep today's behavior.

BEGIN;

ALTER TABLE ai_invoke_paused_chains ADD COLUMN continuation_step_timeout_sec INTEGER;

COMMIT;
