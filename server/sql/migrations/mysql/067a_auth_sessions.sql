CREATE TABLE auth_sessions (
  session_id VARCHAR(191) PRIMARY KEY,
  user_id VARCHAR(191) NOT NULL,
  created_at TEXT NOT NULL,
  last_used_at TEXT NOT NULL,
  device_label TEXT NULL,
  ip_display TEXT NULL,
  revoked_at TEXT NULL,
  revoke_reason VARCHAR(32) NULL,
  CONSTRAINT chk_auth_sessions_revoke_reason CHECK (revoke_reason IN ('logout','remote','revoke_others','password_change','reuse_detected','admin')),
  CONSTRAINT fk_auth_sessions_user FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
);
CREATE INDEX idx_auth_sessions_user ON auth_sessions(user_id, revoked_at(191));
ALTER TABLE refresh_tokens ADD COLUMN session_id VARCHAR(191) NULL;
ALTER TABLE refresh_tokens ADD CONSTRAINT fk_refresh_tokens_session FOREIGN KEY (session_id) REFERENCES auth_sessions(session_id) ON DELETE SET NULL;
CREATE INDEX idx_refresh_tokens_session ON refresh_tokens(session_id);
