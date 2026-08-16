-- 075_test_run_cases_fk_repair.sql
-- flowgate.default.0358 T0004 follow-up: repair databases that already applied the first,
-- broken revision of 074_test_run_cancel_status.sql.
--
-- That revision rebuilt test_runs via "ALTER TABLE test_runs RENAME TO
-- test_runs_before_cancel_status". Since SQLite 3.25 such a rename also rewrites every
-- REFERENCES clause in other tables that points at the renamed table (independently of
-- PRAGMA foreign_keys), so test_run_cases.run_id ended up as
--     REFERENCES "test_runs_before_cancel_status"(run_id) ON DELETE CASCADE
-- and the backup table was then dropped. With PRAGMA foreign_keys = ON — which
-- db/connection.py turns on for every write transaction — the first INSERT into
-- test_run_cases fails with "no such table: main.test_runs_before_cancel_status", so
-- starting any test run 500s. Re-running the migrations against an empty database does
-- not expose it either: PRAGMA foreign_key_check only resolves parents for rows that
-- exist, so on a freshly migrated (still empty) test_run_cases it reports clean.
--
-- 074 is already recorded in `migrations` on those databases and will never re-run, so
-- the repair has to be its own migration. It unconditionally rebuilds test_run_cases with
-- the correct parent reference; on a healthy database (fixed 074, or a fresh install) the
-- rebuild is a faithful copy and changes nothing observable. Rows, ids (and therefore the
-- AUTOINCREMENT high-water mark), the UNIQUE(run_id, case_no) constraint, the kind/result
-- CHECKs and idx_test_run_cases_run are all preserved.
--
-- Safe rebuild order (same as the fixed 074): build under a temporary name, copy, DROP the
-- original, rename into place. No table references test_run_cases, so dropping it cannot
-- cascade and the rename rewrites nothing.

PRAGMA foreign_keys = OFF;

BEGIN;

CREATE TABLE test_run_cases_new (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id       TEXT    NOT NULL REFERENCES test_runs(run_id) ON DELETE CASCADE,
    kind         TEXT    NOT NULL DEFAULT 'case'
                         CHECK (kind IN ('case','setup','service','wait','teardown')),
    case_no      TEXT    NOT NULL,
    case_title   TEXT    NOT NULL,
    cmd          TEXT    NOT NULL,
    expect       TEXT    NOT NULL,
    result       TEXT    CHECK (result IN ('pass','fail','timeout')),
    exit_code    INTEGER,
    duration_ms  INTEGER,
    output_tail  TEXT,
    finished_at  TEXT,
    UNIQUE(run_id, case_no)
);

INSERT INTO test_run_cases_new (
    id, run_id, kind, case_no, case_title, cmd, expect, result,
    exit_code, duration_ms, output_tail, finished_at
)
SELECT
    id, run_id, kind, case_no, case_title, cmd, expect, result,
    exit_code, duration_ms, output_tail, finished_at
FROM test_run_cases;

DROP TABLE test_run_cases;

ALTER TABLE test_run_cases_new RENAME TO test_run_cases;

CREATE INDEX IF NOT EXISTS idx_test_run_cases_run
    ON test_run_cases(run_id, id);

COMMIT;

PRAGMA foreign_keys = ON;
