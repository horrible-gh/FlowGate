-- flowgate.v02.0138: persistent allocation and immutable execution ownership.
-- Applying the schema does NOT enable admission. Drain ALL shared issuers and
-- call ai_run_identity.initialize with their complete scratch inventory first.
CREATE TABLE ai_run_id_counters (
    utc_date VARCHAR(8) PRIMARY KEY,
    serial INTEGER NOT NULL CHECK (serial >= 0 AND serial <= 999999)
);
CREATE TABLE ai_run_id_contract (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    version INTEGER NOT NULL
);
INSERT INTO ai_run_id_contract (singleton, version) VALUES (1, 0);
CREATE TABLE ai_run_owners (
    run_id VARCHAR(32) PRIMARY KEY,
    execution_nonce VARCHAR(36) NOT NULL UNIQUE,
    project_id VARCHAR(255) NOT NULL,
    group_id VARCHAR(255) NOT NULL,
    started_at VARCHAR(64) NOT NULL
);
ALTER TABLE ai_invoke_runs ADD COLUMN execution_nonce VARCHAR(36);
