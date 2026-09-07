-- Durable provider evidence for AI-authored document reviews. Existing rows remain NULL.
ALTER TABLE document_reviews ADD COLUMN review_run_id TEXT NULL;
ALTER TABLE document_reviews ADD COLUMN requested_provider_id TEXT NULL;
ALTER TABLE document_reviews ADD COLUMN actual_provider_id TEXT NULL;
ALTER TABLE document_reviews ADD COLUMN actual_provider_name TEXT NULL;
ALTER TABLE document_reviews ADD COLUMN provider_source TEXT NULL;
ALTER TABLE document_reviews ADD COLUMN attempt_no INTEGER NULL;
ALTER TABLE document_reviews ADD COLUMN fallback_used INTEGER NULL CHECK (fallback_used IN (0, 1));
CREATE INDEX IF NOT EXISTS idx_document_reviews_run ON document_reviews(review_run_id);
