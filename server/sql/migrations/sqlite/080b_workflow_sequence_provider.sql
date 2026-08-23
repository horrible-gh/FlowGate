-- 080b_workflow_sequence_provider.sql
-- flowgate.default.0408 DB0009: persist the provider selected for each workflow step.
-- Additive only; old rows remain NULL/NULL and no provider FK or index is created.

BEGIN;

ALTER TABLE workflow_sequence_items
    ADD COLUMN provider_id TEXT DEFAULT NULL;

ALTER TABLE workflow_sequence_items
    ADD COLUMN provider_display_name TEXT DEFAULT NULL;

COMMIT;