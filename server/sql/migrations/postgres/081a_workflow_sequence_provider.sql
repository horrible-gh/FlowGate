-- 081a_workflow_sequence_provider.sql
-- flowgate.default.0408 DB0009: persist the provider selected for each workflow step.
-- Additive only; old rows remain NULL/NULL and no provider FK or index is created.
--
-- flowgate.default.0452: this file arrived as 080_workflow_sequence_provider.sql, moved to
-- 081_workflow_sequence_provider.sql when T0007 resolved its collision with
-- 080_ai_invoke_prompt_audit.sql (see migration_renames.RENAMES), and collided again — this
-- time with 081_document_origin_snapshot.sql, added independently by flowgate.default.0410
-- in a parallel branch that could not see this file's already-claimed 081. A real
-- `DatabaseMigrator` reboot surfaced the collision as `duplicate column name: provider_id`,
-- so it moves once more, to 081a, keeping its place between 081_document_origin_snapshot.sql
-- and 082_document_origin_backfill.sql.

ALTER TABLE workflow_sequence_items
    ADD COLUMN provider_id TEXT DEFAULT NULL;

ALTER TABLE workflow_sequence_items
    ADD COLUMN provider_display_name VARCHAR(191) DEFAULT NULL;

ALTER TABLE workflow_sequence_items
    ADD CONSTRAINT ck_wfseq_items_provider_pair
        CHECK (provider_display_name IS NULL OR provider_id IS NOT NULL);