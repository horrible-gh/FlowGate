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
-- The CHECK that governs status is found in the catalog, dropped and added again with 'queued'
-- (the 159 / 195 shape). Rows are not touched.
DO $$
DECLARE
    r record;
BEGIN
    FOR r IN
        SELECT c.conname
        FROM pg_constraint c
        JOIN pg_class t ON t.oid = c.conrelid
        JOIN pg_namespace n ON n.oid = t.relnamespace
        JOIN pg_attribute a
          ON a.attrelid = t.oid
         AND a.attnum = ANY (c.conkey)
        WHERE n.nspname = current_schema()
          AND t.relname = 'tr_self_check_runs'
          AND c.contype = 'c'
          AND a.attname = 'status'
    LOOP
        EXECUTE format('ALTER TABLE %I.tr_self_check_runs DROP CONSTRAINT %I', current_schema(), r.conname);
    END LOOP;
END $$;

ALTER TABLE tr_self_check_runs
    ADD CONSTRAINT tr_self_check_runs_status_check
    CHECK (status IN ('queued', 'pending', 'running', 'completed', 'failed', 'cancelled'));

ALTER TABLE tr_self_check_runs ADD COLUMN enqueued_at TEXT NULL;
ALTER TABLE tr_self_check_runs ADD COLUMN snapshot_path TEXT NULL;
ALTER TABLE tr_self_check_runs ADD COLUMN queue_key TEXT NULL;
ALTER TABLE tr_self_check_runs ADD COLUMN queue_lease_owner TEXT NULL;
ALTER TABLE tr_self_check_runs ADD COLUMN queue_lease_until TEXT NULL;
ALTER TABLE tr_self_check_runs ADD COLUMN queue_requeue_count INTEGER NOT NULL DEFAULT 0 CHECK (queue_requeue_count >= 0);
ALTER TABLE tr_self_check_runs ADD COLUMN queue_order TEXT NULL;
ALTER TABLE tr_self_check_runs ADD COLUMN queue_claimed_at TEXT NULL;

CREATE INDEX idx_selfcheck_queue ON tr_self_check_runs (status, queue_order);
CREATE INDEX idx_selfcheck_queue_key ON tr_self_check_runs (queue_key, status);
