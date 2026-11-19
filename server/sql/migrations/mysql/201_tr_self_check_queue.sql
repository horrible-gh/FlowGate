-- 201_tr_self_check_queue.sql
-- flowgate.v02test.0053 1-B (T0005): the Self-check queue.
--
-- run_self_check no longer runs at once under the Group lock (G). It records the request as
-- status 'queued' with a copy (snapshot) of the worktree taken at that moment, and returns.
-- Queue workers inside the server start queued runs oldest first, alternating between
-- projects, inside the existing host processor cap; the run executes in its snapshot and
-- never takes G, so a Group may have any number of runs queued or running.
--
-- tr_self_check_runs:
--   status               gains 'queued' (waiting for a queue worker; holds no reservation).
--   enqueued_at          the wait clock: when the request was queued (again, after a system
--                        re-queue). NULL =
--                        a run admitted before this migration (worktree run under G).
--   snapshot_path        the run's snapshot folder (the tree it executes in, plus meta.json).
--   queue_key            the card the request came from (Graph attempt, TR or draft owner):
--                        a newer request of the same card cancels its older queued ones.
--   queue_lease_owner    the server instance whose queue worker claimed the run.
--   queue_lease_until    the claim's lease; renewed while the worker drives the run. A claim
--                        whose lease lapsed before any process was spawned goes back to the
--                        queue for another worker.
--   queue_requeue_count  system re-queues after the wait limit (at most one).
--   queue_order          the request's place in the queue (UTC, microseconds), set once: a
--                        system re-queue keeps it, so the oldest request still goes first.
--   queue_claimed_at     when a queue worker last claimed the run (UTC, microseconds); the
--                        project whose last claim is the oldest goes next.
-- Existing rows: all new columns NULL / 0; nothing is backfilled.
--
-- The CHECK that governs status is found in information_schema, dropped and added again with
-- 'queued' (the 159 / 195 shape). Rows are not touched.
SET @fg_chk := (
  SELECT tc.CONSTRAINT_NAME FROM information_schema.TABLE_CONSTRAINTS tc
  JOIN information_schema.CHECK_CONSTRAINTS cc ON cc.CONSTRAINT_SCHEMA=tc.CONSTRAINT_SCHEMA AND cc.CONSTRAINT_NAME=tc.CONSTRAINT_NAME
  WHERE tc.CONSTRAINT_SCHEMA=DATABASE() AND tc.TABLE_NAME='tr_self_check_runs' AND tc.CONSTRAINT_TYPE='CHECK'
    AND cc.CHECK_CLAUSE LIKE '%`status`%' AND cc.CHECK_CLAUSE LIKE '%pending%' LIMIT 1
);
SET @fg_sql := IF(@fg_chk IS NULL, 'SELECT 1', CONCAT('ALTER TABLE tr_self_check_runs DROP CHECK `', REPLACE(@fg_chk,'`','``'), '`'));
PREPARE fg_stmt FROM @fg_sql; EXECUTE fg_stmt; DEALLOCATE PREPARE fg_stmt;

ALTER TABLE tr_self_check_runs ADD CONSTRAINT tr_self_check_runs_status_check
    CHECK (status IN ('queued', 'pending', 'running', 'completed', 'failed', 'cancelled'));

ALTER TABLE tr_self_check_runs ADD COLUMN enqueued_at VARCHAR(40) NULL;
ALTER TABLE tr_self_check_runs ADD COLUMN snapshot_path MEDIUMTEXT NULL;
ALTER TABLE tr_self_check_runs ADD COLUMN queue_key VARCHAR(191) NULL;
ALTER TABLE tr_self_check_runs ADD COLUMN queue_lease_owner VARCHAR(191) NULL;
ALTER TABLE tr_self_check_runs ADD COLUMN queue_lease_until VARCHAR(40) NULL;
ALTER TABLE tr_self_check_runs ADD COLUMN queue_requeue_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE tr_self_check_runs ADD COLUMN queue_order VARCHAR(40) NULL;
ALTER TABLE tr_self_check_runs ADD COLUMN queue_claimed_at VARCHAR(40) NULL;

CREATE INDEX idx_selfcheck_queue ON tr_self_check_runs (status, queue_order);
CREATE INDEX idx_selfcheck_queue_key ON tr_self_check_runs (queue_key, status);
