ALTER TABLE user_conversation_settings ADD COLUMN timeout_minutes INTEGER NULL CHECK (timeout_minutes BETWEEN 1 AND 10080);
