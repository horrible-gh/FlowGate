CREATE TABLE IF NOT EXISTS project_ai_leases (
  project_id VARCHAR(255) PRIMARY KEY,
  run_id VARCHAR(255) NOT NULL UNIQUE,
  state VARCHAR(16) NOT NULL,
  acquired_at VARCHAR(64) NOT NULL,
  heartbeat_at VARCHAR(64) NOT NULL,
  expires_at VARCHAR(64) NOT NULL,
  INDEX idx_project_ai_leases_expiry (expires_at)
);