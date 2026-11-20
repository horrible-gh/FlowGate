-- 135_operation_job_group_index.sql
-- flowgate.default.0669 (unit 6b): index for querying jobs by Group.
-- Approval progress queries (approval_in_flight in finalize status) and approval-retry paths
-- find nonterminal final_approval_publish jobs in the same Group. Additive; no data changes.

CREATE INDEX idx_job_group ON operation_job (group_id, kind, status);
