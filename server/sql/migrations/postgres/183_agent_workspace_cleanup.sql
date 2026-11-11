-- flowgate.v02test.0019: per-Agent workspace cleanup policy sent in the heartbeat success response
-- (FlowGate-Agent docs/workspace-cleanup-contract.md). Existing rows get ON / 24 hours.
ALTER TABLE agents ADD COLUMN workspace_cleanup_enabled BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE agents ADD COLUMN workspace_cleanup_retention_hours INTEGER NOT NULL DEFAULT 24 CHECK(workspace_cleanup_retention_hours BETWEEN 1 AND 720);
