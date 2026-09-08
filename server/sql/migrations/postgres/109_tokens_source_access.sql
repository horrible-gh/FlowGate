-- 109_tokens_source_access.sql
-- flowgate.default.0515: token row에 발행 시 확정된 CH capability를 고정 저장한다.

ALTER TABLE tokens
    ADD COLUMN source_access TEXT
        CHECK (source_access IN ('read', 'read_write'));