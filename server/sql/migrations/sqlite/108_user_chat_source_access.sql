-- 108_user_chat_source_access.sql
-- flowgate.default.0515: per-user source-access mode + edit_once one-shot claim marker.
-- D0006 §2.1 (분리 저장소), L0007 §2.4.5 (claim 마커 요구).

BEGIN;

CREATE TABLE IF NOT EXISTS user_chat_source_access (
    user_id             TEXT NOT NULL PRIMARY KEY
                             REFERENCES users(user_id) ON DELETE CASCADE,
    source_access_mode  TEXT NOT NULL
                             CHECK (source_access_mode IN ('read_only', 'edit', 'edit_once')),
    one_shot_token_id   TEXT
                             REFERENCES tokens(token_id) ON DELETE RESTRICT,
    one_shot_claimed_at TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    CHECK ((one_shot_token_id IS NULL) = (one_shot_claimed_at IS NULL))
);

CREATE INDEX IF NOT EXISTS idx_ucsa_one_shot_token_id
    ON user_chat_source_access(one_shot_token_id);

COMMIT;
