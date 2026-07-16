CREATE TABLE auth_sessions (
  session_id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
  created_at TEXT NOT NULL,
  last_used_at TEXT NOT NULL,
  device_label TEXT,
  ip_display TEXT,
  revoked_at TEXT,
  revoke_reason TEXT CHECK (revoke_reason IN ('logout','remote','revoke_others','password_change','reuse_detected','admin'))
);
CREATE INDEX idx_auth_sessions_user ON auth_sessions(user_id, revoked_at);
ALTER TABLE refresh_tokens ADD COLUMN session_id TEXT REFERENCES auth_sessions(session_id) ON DELETE SET NULL;
CREATE INDEX idx_refresh_tokens_session ON refresh_tokens(session_id);
