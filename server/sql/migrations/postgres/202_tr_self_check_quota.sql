-- 0053 1-C: durable execution-stage quota; old runs remain unmetered.
ALTER TABLE tr_self_check_runs ADD COLUMN verify_stage_key TEXT NULL;
ALTER TABLE tr_self_check_runs ADD COLUMN verify_max_runs INTEGER NULL;
ALTER TABLE tr_self_check_runs ADD COLUMN verify_timeout_sec INTEGER NULL;
ALTER TABLE tr_self_check_runs ADD COLUMN verify_started_count INTEGER NOT NULL DEFAULT 0;
CREATE INDEX idx_selfcheck_verify_stage ON tr_self_check_runs (verify_stage_key);
