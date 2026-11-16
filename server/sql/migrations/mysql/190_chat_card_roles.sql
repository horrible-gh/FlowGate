-- 190_chat_card_roles.sql  (mysql)
-- flowgate.v02gui.0028: the chat card AI role API (flowgate.chat_control.card_roles). See the sqlite file.
--   chat_placed_cards     one row per work card a chat placed: the CH conversation document that placed it.
--   chat_card_role_audit  one row per requested role of every successful role PATCH of the chat API.
-- MySQL has no CREATE INDEX IF NOT EXISTS, so the indexes are declared inside CREATE TABLE IF NOT EXISTS
-- (same names and columns as the other dialects).

CREATE TABLE IF NOT EXISTS chat_placed_cards (
    group_id     VARCHAR(191) NOT NULL,
    node_key     VARCHAR(200) NOT NULL,
    chat_doc_id  VARCHAR(191) NOT NULL,
    token_id     VARCHAR(191) NULL,
    ai_run_id    VARCHAR(64)  NULL,
    placed_by    VARCHAR(191) NULL,
    placed_at    VARCHAR(40)  NOT NULL,
    PRIMARY KEY (group_id, node_key),
    KEY idx_chat_placed_cards_chat (group_id, chat_doc_id),
    CONSTRAINT fk_cpc_group FOREIGN KEY (group_id) REFERENCES groups(group_id) ON DELETE CASCADE
) ROW_FORMAT=DYNAMIC;

CREATE TABLE IF NOT EXISTS chat_card_role_audit (
    audit_id          VARCHAR(64)  NOT NULL,
    group_id          VARCHAR(191) NOT NULL,
    node_key          VARCHAR(200) NOT NULL,
    role              VARCHAR(16)  NOT NULL,
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
    KEY idx_chat_card_role_audit_card (group_id, node_key, created_at),
    CONSTRAINT fk_ccra_group FOREIGN KEY (group_id) REFERENCES groups(group_id) ON DELETE CASCADE,
    CONSTRAINT ck_ccra_role CHECK (role IN ('instruction', 'execution', 'review')),
    CONSTRAINT ck_ccra_changed CHECK (changed IN (0, 1))
) ROW_FORMAT=DYNAMIC;

-- ── DOWN (rollback) ──────────────────────────────────────────────────────────
-- DROP TABLE IF EXISTS chat_card_role_audit;
-- DROP TABLE IF EXISTS chat_placed_cards;
-- ── END DOWN ─────────────────────────────────────────────────────────────────
