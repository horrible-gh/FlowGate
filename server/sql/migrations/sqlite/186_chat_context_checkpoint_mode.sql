-- 186_chat_context_checkpoint_mode.sql
-- flowgate.v02test.0025: the [checkpoint] context mode.
--   user_chat_settings.context_mode  accepts 'checkpoint' (the chat's checkpoint decides the window; the last 5
--                                    turns without one). 'recent' and 'all' are unchanged.
--   user_conversation_settings.context_checkpoint_kind
--                                    'include' = send from the checkpoint message, 'exclude' = from the next
--                                    message. NULL reads as 'include' (checkpoints saved under 185).
--
-- SQLite cannot widen a CHECK in place, so user_chat_settings is rebuilt: every column and every row is kept.
-- Nothing references user_chat_settings; the replacement is built under a new name, the data copied, the
-- original dropped and the replacement renamed into place (the same shape as 159).
PRAGMA foreign_keys=OFF;
BEGIN;
DROP TABLE IF EXISTS user_chat_settings_new;

CREATE TABLE user_chat_settings_new (
    user_id       TEXT    NOT NULL PRIMARY KEY
                          REFERENCES users(user_id) ON DELETE CASCADE,
    send_action   TEXT    NOT NULL
                          CHECK (send_action IN ('copy_mention', 'invoke_ai', 'none')),
    context_mode  TEXT    NOT NULL
                          CHECK (context_mode IN ('recent', 'all', 'checkpoint')),
    context_turns INTEGER NOT NULL
                          CHECK (context_turns >= 1),
    created_at    TEXT    NOT NULL,
    updated_at    TEXT    NOT NULL
);

INSERT INTO user_chat_settings_new (user_id, send_action, context_mode, context_turns, created_at, updated_at)
SELECT user_id, send_action, context_mode, context_turns, created_at, updated_at FROM user_chat_settings;

DROP TABLE user_chat_settings;
ALTER TABLE user_chat_settings_new RENAME TO user_chat_settings;

ALTER TABLE user_conversation_settings ADD COLUMN context_checkpoint_kind TEXT;
COMMIT;
PRAGMA foreign_keys=ON;
