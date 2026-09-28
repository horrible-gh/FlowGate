-- flowgate.default.0554 T0008: per-step execution settings snapshot.
ALTER TABLE workflow_sequence_items
    ADD COLUMN review_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE workflow_sequence_items
    ADD COLUMN reviewer_provider_id TEXT DEFAULT NULL;
ALTER TABLE workflow_sequence_items
    ADD COLUMN reviewer_provider_display_name VARCHAR(191) DEFAULT NULL;
ALTER TABLE workflow_sequence_items
    ADD COLUMN pre_instruction_text TEXT DEFAULT NULL;
ALTER TABLE workflow_sequence_items
    ADD COLUMN pre_instruction_attachment_json TEXT DEFAULT NULL;

ALTER TABLE workflow_sequence_items
    ADD CONSTRAINT ck_wfseq_items_reviewer_pair
        CHECK (reviewer_provider_display_name IS NULL OR reviewer_provider_id IS NOT NULL);
