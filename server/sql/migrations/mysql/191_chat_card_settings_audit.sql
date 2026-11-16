-- 191_chat_card_settings_audit.sql  (mysql)
-- flowgate.v02gui.0032: the run settings a chat may change on the cards it placed. See the sqlite file.
--   chat_card_setting_audit  one row per requested setting of every successful chat change of those settings.
-- MySQL has no CREATE INDEX IF NOT EXISTS, so the index is declared inside CREATE TABLE IF NOT EXISTS
-- (same name and columns as the other dialects).

CREATE TABLE IF NOT EXISTS chat_card_setting_audit (
    audit_id          VARCHAR(64)  NOT NULL,
    group_id          VARCHAR(191) NOT NULL,
    node_key          VARCHAR(200) NOT NULL,
    field             VARCHAR(32)  NOT NULL,
    before_json       TEXT         NULL,
    after_json        TEXT         NULL,
    changed           INTEGER      NOT NULL,
    settings_version  INTEGER      NULL,
    token_id          VARCHAR(191) NULL,
    ai_run_id         VARCHAR(64)  NULL,
    chat_doc_id       VARCHAR(191) NOT NULL,
    actor_user_id     VARCHAR(191) NOT NULL,
    created_at        VARCHAR(40)  NOT NULL,
    PRIMARY KEY (audit_id),
    KEY idx_chat_card_setting_audit_card (group_id, node_key, created_at),
    CONSTRAINT fk_ccsa_group FOREIGN KEY (group_id) REFERENCES groups(group_id) ON DELETE CASCADE,
    CONSTRAINT ck_ccsa_field CHECK (field IN ('review_count', 'restart', 'time_limit')),
    CONSTRAINT ck_ccsa_changed CHECK (changed IN (0, 1))
) ROW_FORMAT=DYNAMIC;

-- ── DOWN (rollback) ──────────────────────────────────────────────────────────
-- DROP TABLE IF EXISTS chat_card_setting_audit;
-- ── END DOWN ─────────────────────────────────────────────────────────────────
