-- 074_test_run_cancel_status.sql
-- flowgate.default.0358 T0004: add 'cancelling'/'cancelled' to test_runs.status so a
-- user-initiated cancel is tracked distinctly from passed/failed and never mistaken for
-- an INFRA failure by the auto-recovery loop (NR0003 상태 어휘와 스키마 결정, option A).
--
-- SQLite CHECK constraints cannot be altered in place, so the table is rebuilt: every
-- existing column (including 069's source_root/source_root_kind), the doc_id/tsr_doc_id
-- FKs, all data, and idx_test_runs_doc are preserved.
--
-- IMPORTANT — do NOT rebuild this table with the usual
-- "ALTER TABLE test_runs RENAME TO test_runs_before_...; CREATE TABLE test_runs ..."
-- shape used by 042/052 for `tokens`. Since SQLite 3.25 an ALTER TABLE ... RENAME TO
-- also rewrites every REFERENCES clause in *other* tables that points at the renamed
-- table, and it does so regardless of `PRAGMA foreign_keys` (only legacy_alter_table
-- suppresses it). `test_run_cases.run_id REFERENCES test_runs(run_id)` (052) would be
-- rewritten to the throwaway backup name and left dangling after it is dropped, so the
-- very first INSERT into test_run_cases fails with
-- "no such table: main.test_runs_before_cancel_status" — i.e. no test run can start.
-- `tokens` never hit this only because no other table has a REFERENCES clause on it.
--
-- Instead use SQLite's documented table-rebuild order: build the replacement under a
-- temporary name, copy, DROP the original, then rename the replacement into place.
-- Nothing references `test_runs_new`, so that rename rewrites nothing, and
-- test_run_cases keeps pointing at `test_runs` — which now resolves to the new table.
-- 075_test_run_cases_fk_repair.sql repairs databases that already applied the earlier,
-- broken version of this migration.

PRAGMA foreign_keys = OFF;

BEGIN;

CREATE TABLE test_runs_new (
    run_id       TEXT PRIMARY KEY,
    doc_id       TEXT    NOT NULL REFERENCES documents(doc_id) ON DELETE CASCADE,
    revision_no  INTEGER NOT NULL,
    status       TEXT    NOT NULL
                     CHECK (status IN ('running','passed','failed','cancelling','cancelled')),
    triggered_via TEXT   NOT NULL CHECK (triggered_via IN ('ui','token')),
    runner_id    TEXT    NOT NULL,
    case_total   INTEGER NOT NULL DEFAULT 0,
    case_passed  INTEGER NOT NULL DEFAULT 0,
    case_failed  INTEGER NOT NULL DEFAULT 0,
    error        TEXT,
    picked_at    TEXT,
    started_at   TEXT,
    finished_at  TEXT,
    port         INTEGER,
    tsr_doc_id   TEXT REFERENCES documents(doc_id) ON DELETE SET NULL,
    created_at   TEXT    NOT NULL,
    source_root  TEXT,
    source_root_kind TEXT
);

INSERT INTO test_runs_new (
    run_id, doc_id, revision_no, status, triggered_via, runner_id,
    case_total, case_passed, case_failed, error, picked_at, started_at,
    finished_at, port, tsr_doc_id, created_at, source_root, source_root_kind
)
SELECT
    run_id, doc_id, revision_no, status, triggered_via, runner_id,
    case_total, case_passed, case_failed, error, picked_at, started_at,
    finished_at, port, tsr_doc_id, created_at, source_root, source_root_kind
FROM test_runs;

DROP TABLE test_runs;

ALTER TABLE test_runs_new RENAME TO test_runs;

CREATE INDEX IF NOT EXISTS idx_test_runs_doc
    ON test_runs(doc_id, created_at DESC);

COMMIT;

PRAGMA foreign_keys = ON;
