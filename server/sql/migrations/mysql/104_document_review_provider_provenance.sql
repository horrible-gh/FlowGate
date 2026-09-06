-- Durable provider evidence for AI-authored document reviews. Existing rows remain NULL.
ALTER TABLE document_reviews
    ADD COLUMN review_run_id VARCHAR(191) NULL,
    ADD COLUMN requested_provider_id VARCHAR(191) NULL,
    ADD COLUMN actual_provider_id VARCHAR(191) NULL,
    ADD COLUMN actual_provider_name TEXT NULL,
    ADD COLUMN provider_source VARCHAR(64) NULL,
    ADD COLUMN attempt_no INTEGER NULL,
    ADD COLUMN fallback_used BOOLEAN NULL;
CREATE INDEX idx_document_reviews_run ON document_reviews(review_run_id);
