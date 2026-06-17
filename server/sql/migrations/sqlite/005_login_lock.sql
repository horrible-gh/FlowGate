-- 005: add login lock columns

ALTER TABLE users ADD COLUMN login_failed_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE users ADD COLUMN login_locked_until TEXT;
