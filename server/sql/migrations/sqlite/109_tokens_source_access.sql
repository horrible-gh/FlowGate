-- 109_tokens_source_access.sql
-- flowgate.default.0515: persist the CH capability fixed at issuance on the token row.

ALTER TABLE tokens
    ADD COLUMN source_access TEXT
        CHECK (source_access IN ('read', 'read_write'));
