-- 016_tokens_scratch_dir.sql
-- Add the scratch_dir column to the tokens table (fix for R015 R0.1 D9 defect)

ALTER TABLE tokens ADD COLUMN scratch_dir TEXT;