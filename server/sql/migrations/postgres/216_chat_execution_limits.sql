-- F108-5: preserve existing chat durations and add per-user command limits.
ALTER TABLE user_conversation_settings ADD COLUMN timeout_seconds INTEGER NULL CHECK (timeout_seconds BETWEEN 60 AND 604800);
ALTER TABLE user_conversation_settings ADD COLUMN command_timeout_seconds INTEGER NULL CHECK (command_timeout_seconds BETWEEN 0 AND 1800);
UPDATE user_conversation_settings SET timeout_seconds = timeout_minutes * 60;
ALTER TABLE chat_command_requests DROP CONSTRAINT chat_command_requests_timeout_seconds_check;
ALTER TABLE chat_command_requests ADD CONSTRAINT chat_command_requests_timeout_seconds_check CHECK (timeout_seconds >= 0);
