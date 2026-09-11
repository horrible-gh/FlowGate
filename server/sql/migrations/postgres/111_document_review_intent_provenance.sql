-- Admission provenance for new AI review rows. Existing rows remain NULL.
ALTER TABLE document_reviews ADD COLUMN IF NOT EXISTS review_intent TEXT NULL CHECK (review_intent IN ('normal', 'rerun'));
ALTER TABLE document_reviews ADD COLUMN IF NOT EXISTS superseded_review_id INTEGER NULL;
