BEGIN;

-- 084_ai_invoke_provider_pin.sql
-- flowgate.default.0435 T0004: preserve whether the person explicitly pinned the provider.
-- Additive and nullable: NULL on legacy paused rows means "not pinned".

ALTER TABLE ai_invoke_paused_chains ADD COLUMN continuation_provider_pinned INTEGER;

COMMIT;
