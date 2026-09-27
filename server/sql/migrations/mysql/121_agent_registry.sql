CREATE TABLE agents (
  agent_id VARCHAR(64) PRIMARY KEY,
  name VARCHAR(255) NOT NULL,
  enabled BOOLEAN NOT NULL DEFAULT TRUE,
  location VARCHAR(16) NOT NULL,
  connection_mode VARCHAR(32) NULL,
  local_executable_path TEXT NULL,
  configured_capabilities TEXT NOT NULL,
  reported_capabilities TEXT NOT NULL,
  last_seen_at VARCHAR(64) NULL,
  agent_version VARCHAR(64) NULL,
  protocol_version VARCHAR(64) NULL,
  os VARCHAR(64) NULL,
  architecture VARCHAR(64) NULL,
  created_at VARCHAR(64) NOT NULL,
  updated_at VARCHAR(64) NOT NULL
);
CREATE TABLE agent_enrollments (
  enrollment_id VARCHAR(64) PRIMARY KEY,
  agent_id VARCHAR(64) NOT NULL,
  code_hash CHAR(64) NOT NULL UNIQUE,
  expires_at VARCHAR(64) NOT NULL,
  consumed_at VARCHAR(64) NULL,
  revoked_at VARCHAR(64) NULL,
  created_at VARCHAR(64) NOT NULL,
  INDEX idx_agent_enrollments_agent(agent_id),
  FOREIGN KEY (agent_id) REFERENCES agents(agent_id)
);
CREATE TABLE agent_credentials (
  credential_id VARCHAR(64) PRIMARY KEY,
  agent_id VARCHAR(64) NOT NULL,
  secret_hash CHAR(64) NOT NULL UNIQUE,
  created_at VARCHAR(64) NOT NULL,
  revoked_at VARCHAR(64) NULL,
  INDEX idx_agent_credentials_agent(agent_id),
  FOREIGN KEY (agent_id) REFERENCES agents(agent_id)
);
