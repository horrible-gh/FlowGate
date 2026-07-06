-- 058_return_point_root_status.sql
-- Reverse time-machine: record the workflow root's pre-rewind status on the return point so a
-- restore-to-front can honestly re-declare it (wf_done stays wf_done; a mid-flight wf_in_progress
-- is restored as wf_in_progress rather than falsely finalized). Additive nullable column; legacy
-- return points read NULL and fall back to the old wf_done finalize behaviour (0158, B0001).
-- Pure ADD COLUMN (like 043/054); no table rebuild needed for an additive nullable column.

ALTER TABLE workflow_return_points ADD COLUMN root_prev_status TEXT;
