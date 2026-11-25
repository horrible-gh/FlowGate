-- Personal title AI and finite repeat ceiling; no row means system defaults.
CREATE TABLE IF NOT EXISTS user_ai_preferences (
    user_id TEXT PRIMARY KEY REFERENCES users(user_id) ON DELETE CASCADE,
    title_provider VARCHAR(191) NOT NULL DEFAULT 'auto',
    repeat_count_max INTEGER NULL,
    updated_at VARCHAR(64) NOT NULL
);
