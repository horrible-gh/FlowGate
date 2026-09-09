-- 076_user_chat_settings.sql
-- flowgate.default.0362: per-user chat settings (send action, conversation context range).

CREATE TABLE IF NOT EXISTS user_chat_settings (
    user_id       TEXT    NOT NULL PRIMARY KEY
                          REFERENCES users(user_id) ON DELETE CASCADE,
    send_action   TEXT    NOT NULL
                          CHECK (send_action IN ('copy_mention', 'invoke_ai', 'none')),
    context_mode  TEXT    NOT NULL
                          CHECK (context_mode IN ('recent', 'all')),
    context_turns INTEGER NOT NULL
                          CHECK (context_turns >= 1),
    created_at    TEXT    NOT NULL,
    updated_at    TEXT    NOT NULL
);
