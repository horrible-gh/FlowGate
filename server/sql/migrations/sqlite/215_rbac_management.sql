ALTER TABLE teams ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1;

CREATE TABLE IF NOT EXISTS rbac_change_audit (
    change_id VARCHAR(32) NOT NULL PRIMARY KEY,
    actor_id VARCHAR(255),
    operation VARCHAR(80) NOT NULL,
    target_id VARCHAR(512) NOT NULL,
    changed_at VARCHAR(64) NOT NULL
);
