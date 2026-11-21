-- flowgate.v02test.0058: normalized audio, with transcript sent as the turn body.
CREATE TABLE message_audio (
    audio_id VARCHAR(64) PRIMARY KEY,
    flow_id VARCHAR(200) NOT NULL,
    project_id VARCHAR(100) NOT NULL,
    uploaded_by VARCHAR(100) NOT NULL,
    sha256 VARCHAR(64) NOT NULL,
    size INTEGER NOT NULL,
    transcript TEXT NOT NULL,
    state VARCHAR(16) NOT NULL,
    created_at VARCHAR(40) NOT NULL,
    conversation_id VARCHAR(200),
    seq INTEGER
);
CREATE INDEX ix_message_audio_flow ON message_audio(flow_id);
CREATE INDEX ix_message_audio_expiry ON message_audio(state, created_at);
ALTER TABLE conversation_turns ADD COLUMN audio_id VARCHAR(64);
