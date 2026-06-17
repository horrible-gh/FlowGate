-- 002: add TOTP lock columns + create the backup_codes table

ALTER TABLE users ADD COLUMN totp_failed_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE users ADD COLUMN totp_locked_until TEXT;

CREATE TABLE IF NOT EXISTS backup_codes (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    code       TEXT NOT NULL,
    used_at    TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_backup_codes_user ON backup_codes(user_id);
