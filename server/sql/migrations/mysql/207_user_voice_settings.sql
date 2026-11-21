-- flowgate.v02test.0058: per-user voice choices, independent of retention settings.
CREATE TABLE IF NOT EXISTS user_voice_settings (
    user_id VARCHAR(191) NOT NULL PRIMARY KEY REFERENCES users(user_id) ON DELETE CASCADE,
    voice_action TEXT NOT NULL,
    voice_service TEXT NOT NULL,
    voice_model TEXT NOT NULL,
    voice_realtime INTEGER NOT NULL,
    updated_at TEXT NOT NULL
);
