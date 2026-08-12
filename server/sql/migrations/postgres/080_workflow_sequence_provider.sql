-- 080_workflow_sequence_provider.sql
-- flowgate.default.0408 DB0009: persist the provider selected for each workflow step.
-- Additive only; old rows remain NULL/NULL and no provider FK or index is created.

ALTER TABLE workflow_sequence_items
    ADD COLUMN provider_id TEXT DEFAULT NULL;

ALTER TABLE workflow_sequence_items
    ADD COLUMN provider_display_name VARCHAR(191) DEFAULT NULL;

ALTER TABLE workflow_sequence_items
    ADD CONSTRAINT ck_wfseq_items_provider_pair
        CHECK (provider_display_name IS NULL OR provider_id IS NOT NULL);