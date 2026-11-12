-- 185_chat_ai_settings.sql  (postgres)
-- flowgate.v02test.0025: chat AI settings.
--   user_conversation_settings  one row per (user, chat): the AI picked in that chat (NULL = the user's
--                               default) and the message the AI context starts from (NULL = no checkpoint).
--                               Each user keeps their own row, so a personal AI never leaks into another
--                               user's chat and a checkpoint only narrows the context of the user who set it.
--   user_ai_order               each user's display order over system AI and their own AI in one list. Read
--                               by every AI selection list. The fallback chain keeps reading
--                               ai_providers.sort_order (an administrator's reorder updates both together).

CREATE TABLE IF NOT EXISTS user_conversation_settings (
    user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    doc_id TEXT NOT NULL,
    provider_id TEXT REFERENCES ai_providers(provider_id) ON DELETE SET NULL,
    context_checkpoint_seq INTEGER,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (user_id, doc_id)
);

CREATE TABLE IF NOT EXISTS user_ai_order (
    user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    provider_id TEXT NOT NULL REFERENCES ai_providers(provider_id) ON DELETE CASCADE,
    sort_order INTEGER NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (user_id, provider_id)
);
