-- 0061 T#8. 153 is already deployed for execution capacity; use the next suffix.
-- Additive, nullable, no backfill. integration_status and input_source are open keys.
-- integration_status: none/pending/integrating/integrated/conflict/failed
-- input_source: run_base/predecessors/integration_head/document_only
ALTER TABLE graph_node_attempts ADD COLUMN result_commit VARCHAR(64) NULL;
ALTER TABLE graph_node_attempts ADD COLUMN integration_status VARCHAR(32) NULL;
ALTER TABLE graph_node_attempts ADD COLUMN integration_reason TEXT NULL;
ALTER TABLE graph_node_attempts ADD COLUMN integrated_commit VARCHAR(64) NULL;
ALTER TABLE graph_node_attempts ADD COLUMN input_source VARCHAR(32) NULL;
ALTER TABLE graph_node_attempts ADD COLUMN input_attempt_ids TEXT NULL;
ALTER TABLE tr_commit_ledger ADD COLUMN attempt_id VARCHAR(64) NULL;
ALTER TABLE tr_commit_ledger ADD COLUMN node_key VARCHAR(200) NULL;
-- One result per attempt; legacy approval rounds retain NULL and remain unrestricted.
CREATE UNIQUE INDEX idx_trl_attempt ON tr_commit_ledger(attempt_id);
