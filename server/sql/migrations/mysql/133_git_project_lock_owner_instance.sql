-- 133_git_project_lock_owner_instance.sql
-- flowgate.default.0669 (unit 1a) / 0666 0010-DB §2.6·§3 (design number 132 → 133).
-- git_project_lock.owner_instance_id — server instance that inserted the legacy lock row. Empty means DEAD
-- (L 2.6.5 premise P-L1). Unlike other FKs, ON DELETE SET NULL preserves the DEAD meaning when the instance row is deleted.
-- Additive, no backfill (existing NULL rows are remnants of processes predating the bridge).

ALTER TABLE git_project_lock ADD COLUMN owner_instance_id VARCHAR(37) NULL;

CREATE INDEX idx_git_project_lock_owner ON git_project_lock (owner_instance_id);

ALTER TABLE git_project_lock ADD CONSTRAINT fk_gpl_owner_instance
    FOREIGN KEY (owner_instance_id) REFERENCES server_instance(instance_id) ON DELETE SET NULL;
