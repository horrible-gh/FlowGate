-- Durable provider evidence for AI-authored document reviews. Existing rows remain NULL.
ALTER TABLE document_reviews ADD COLUMN IF NOT EXISTS review_run_id TEXT NULL;
ALTER TABLE document_reviews ADD COLUMN IF NOT EXISTS requested_provider_id TEXT NULL;
ALTER TABLE document_reviews ADD COLUMN IF NOT EXISTS actual_provider_id TEXT NULL;
ALTER TABLE document_reviews ADD COLUMN IF NOT EXISTS actual_provider_name TEXT NULL;
ALTER TABLE document_reviews ADD COLUMN IF NOT EXISTS provider_source TEXT NULL;
ALTER TABLE document_reviews ADD COLUMN IF NOT EXISTS attempt_no INTEGER NULL;
ALTER TABLE document_reviews ADD COLUMN IF NOT EXISTS fallback_used BOOLEAN NULL;
CREATE INDEX IF NOT EXISTS idx_document_reviews_run ON document_reviews(review_run_id);
