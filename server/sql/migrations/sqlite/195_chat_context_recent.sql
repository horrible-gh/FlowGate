PRAGMA foreign_keys=OFF;
BEGIN;
DROP TABLE IF EXISTS user_chat_settings_new;

CREATE TABLE user_chat_settings_new (
    user_id       TEXT    NOT NULL PRIMARY KEY
                          REFERENCES users(user_id) ON DELETE CASCADE,
    send_action   TEXT    NOT NULL
                          CHECK (send_action IN ('copy_mention', 'invoke_ai', 'none')),
    context_mode  TEXT    NOT NULL
                          CHECK (context_mode IN ('recent', 'all', 'checkpoint', 'checkpoint_recent')),
    context_turns INTEGER NOT NULL
                          CHECK (context_turns >= 1),
    created_at    TEXT    NOT NULL,
    updated_at    TEXT    NOT NULL
);

INSERT INTO user_chat_settings_new (user_id, send_action, context_mode, context_turns, created_at, updated_at)
SELECT user_id, send_action, context_mode, context_turns, created_at, updated_at FROM user_chat_settings;

DROP TABLE user_chat_settings;
ALTER TABLE user_chat_settings_new RENAME TO user_chat_settings;

ALTER TABLE user_chat_settings ADD COLUMN checkpoint_max_turns INTEGER NOT NULL DEFAULT 10;
COMMIT;
PRAGMA foreign_keys=ON;
