-- 207_user_designer_theme.sql: user taskbar style, independent of layout and retention.
CREATE TABLE IF NOT EXISTS user_designer_theme (
    user_id TEXT NOT NULL PRIMARY KEY REFERENCES users(user_id) ON DELETE CASCADE,
    taskbar_style TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
