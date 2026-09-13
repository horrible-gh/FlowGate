-- Admission provenance for new AI review rows. Existing rows remain NULL.
ALTER TABLE document_reviews
    ADD COLUMN review_intent VARCHAR(16) NULL,
    ADD COLUMN superseded_review_id INTEGER NULL;
