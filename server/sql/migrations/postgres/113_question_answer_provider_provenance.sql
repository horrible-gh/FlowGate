-- 0582 T0005: durable provider evidence for AI-authored Q&A (asker_kind/author_kind='ai').
-- Existing rows remain NULL -- legacy Q&A predates run/provider snapshotting.
ALTER TABLE question_items ADD COLUMN IF NOT EXISTS asker_ai_run_id TEXT NULL;
ALTER TABLE question_items ADD COLUMN IF NOT EXISTS asker_actual_provider_id TEXT NULL;
ALTER TABLE question_items ADD COLUMN IF NOT EXISTS asker_actual_provider_name TEXT NULL;
ALTER TABLE answers ADD COLUMN IF NOT EXISTS author_ai_run_id TEXT NULL;
ALTER TABLE answers ADD COLUMN IF NOT EXISTS author_actual_provider_id TEXT NULL;
ALTER TABLE answers ADD COLUMN IF NOT EXISTS author_actual_provider_name TEXT NULL;
