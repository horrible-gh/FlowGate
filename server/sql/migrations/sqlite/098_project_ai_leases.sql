CREATE TABLE IF NOT EXISTS project_ai_leases (
  project_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL UNIQUE,
  state TEXT NOT NULL CHECK (state IN ('acquiring','active')),
  acquired_at TEXT NOT NULL,
  heartbeat_at TEXT NOT NULL,
  expires_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_project_ai_leases_expiry ON project_ai_leases(expires_at);