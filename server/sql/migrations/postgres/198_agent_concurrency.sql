-- Per-Agent capacity applies to claimed and started jobs together.
ALTER TABLE agents ADD COLUMN max_concurrent_jobs INTEGER NOT NULL DEFAULT 4 CHECK (max_concurrent_jobs BETWEEN 1 AND 32);
ALTER TABLE agent_jobs ADD COLUMN capacity_wait_at TEXT;
