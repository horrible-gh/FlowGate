-- 108_user_chat_source_access.sql
-- flowgate.default.0515: per-user source-access mode + edit_once one-shot claim marker.
-- MySQL은 column definition에 붙인 inline REFERENCES를 parse만 하고 실제 FK를 만들지 않는다
-- (076_user_chat_settings.sql이 이미 그 형태이지만 강제되지 않는다 — rev1 반려 2번).
-- 이 테이블은 077_group_ai_leases.sql / 103_group_ai_lease_events.sql /
-- 091_ai_invoke_document_review_loops.sql과 같은 named CONSTRAINT ... FOREIGN KEY
-- table constraint로 쓴다.

CREATE TABLE IF NOT EXISTS user_chat_source_access (
    user_id             VARCHAR(191) NOT NULL PRIMARY KEY,
    source_access_mode  TEXT NOT NULL
                             CHECK (source_access_mode IN ('read_only', 'edit', 'edit_once')),
    one_shot_token_id   VARCHAR(191),
    one_shot_claimed_at TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    CHECK ((one_shot_token_id IS NULL) = (one_shot_claimed_at IS NULL)),
    CONSTRAINT fk_ucsa_user
        FOREIGN KEY (user_id)
        REFERENCES users(user_id)
        ON DELETE CASCADE,
    CONSTRAINT fk_ucsa_token
        FOREIGN KEY (one_shot_token_id)
        REFERENCES tokens(token_id)
        ON DELETE RESTRICT
);

CREATE INDEX idx_ucsa_one_shot_token_id
    ON user_chat_source_access(one_shot_token_id);
