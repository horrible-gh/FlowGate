-- 109_tokens_source_access.sql
ALTER TABLE tokens
    ADD COLUMN source_access TEXT
        CHECK (source_access IN ('read', 'read_write'));
