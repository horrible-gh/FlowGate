CREATE TABLE agents (
  agent_id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  enabled BOOLEAN NOT NULL DEFAULT TRUE,
  location TEXT NOT NULL CHECK (location IN ('local','remote')),
  connection_mode TEXT NULL CHECK (connection_mode IS NULL OR connection_mode = 'agent_pull'),
  local_executable_path TEXT NULL,
  configured_capabilities TEXT NOT NULL DEFAULT '[]',
  reported_capabilities TEXT NOT NULL DEFAULT '[]',
  last_seen_at TEXT NULL,
  agent_version TEXT NULL,
  protocol_version TEXT NULL,
  os TEXT NULL,
  architecture TEXT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE agent_enrollments (
  enrollment_id TEXT PRIMARY KEY,
  agent_id TEXT NOT NULL REFERENCES agents(agent_id),
  code_hash TEXT NOT NULL UNIQUE,
  expires_at TEXT NOT NULL,
  consumed_at TEXT NULL,
  revoked_at TEXT NULL,
  created_at TEXT NOT NULL
);
CREATE INDEX idx_agent_enrollments_agent ON agent_enrollments(agent_id);
CREATE TABLE agent_credentials (
  credential_id TEXT PRIMARY KEY,
  agent_id TEXT NOT NULL REFERENCES agents(agent_id),
  secret_hash TEXT NOT NULL UNIQUE,
  created_at TEXT NOT NULL,
  revoked_at TEXT NULL
);
CREATE INDEX idx_agent_credentials_agent ON agent_credentials(agent_id);
