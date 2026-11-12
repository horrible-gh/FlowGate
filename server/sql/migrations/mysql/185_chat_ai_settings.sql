-- 185_chat_ai_settings.sql  (mysql)
-- flowgate.v02test.0025: chat AI settings.
--   user_conversation_settings  one row per (user, chat): the AI picked in that chat (NULL = the user's
--                               default) and the message the AI context starts from (NULL = no checkpoint).
--                               Each user keeps their own row, so a personal AI never leaks into another
--                               user's chat and a checkpoint only narrows the context of the user who set it.
--   user_ai_order               each user's display order over system AI and their own AI in one list. Read
--                               by every AI selection list. The fallback chain keeps reading
--                               ai_providers.sort_order (an administrator's reorder updates both together).

CREATE TABLE user_conversation_settings (
    user_id VARCHAR(191) NOT NULL,
    doc_id VARCHAR(191) NOT NULL,
    provider_id VARCHAR(191) NULL,
    context_checkpoint_seq INT NULL,
    updated_at VARCHAR(64) NOT NULL,
    PRIMARY KEY (user_id, doc_id),
    CONSTRAINT fk_ucs_user FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE,
    CONSTRAINT fk_ucs_provider FOREIGN KEY (provider_id) REFERENCES ai_providers(provider_id) ON DELETE SET NULL
);

CREATE TABLE user_ai_order (
    user_id VARCHAR(191) NOT NULL,
    provider_id VARCHAR(191) NOT NULL,
    sort_order INT NOT NULL,
    updated_at VARCHAR(64) NOT NULL,
    PRIMARY KEY (user_id, provider_id),
    CONSTRAINT fk_uao_user FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE,
    CONSTRAINT fk_uao_provider FOREIGN KEY (provider_id) REFERENCES ai_providers(provider_id) ON DELETE CASCADE
);
