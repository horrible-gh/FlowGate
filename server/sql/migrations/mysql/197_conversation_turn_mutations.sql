ALTER TABLE conversation_turns ADD COLUMN message_version INTEGER NOT NULL DEFAULT 1;
ALTER TABLE conversation_turns ADD COLUMN edited_at VARCHAR(40) NULL;
ALTER TABLE conversation_turns ADD COLUMN deleted_at VARCHAR(40) NULL;
