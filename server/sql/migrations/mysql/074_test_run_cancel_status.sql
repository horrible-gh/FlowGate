SET FOREIGN_KEY_CHECKS=0;
-- 074_test_run_cancel_status.sql
-- flowgate.default.0358 T0004: add 'cancelling'/'cancelled' to test_runs.status so a
-- user-initiated cancel is tracked distinctly from passed/failed and never mistaken for
-- an INFRA failure by the auto-recovery loop (NR0003 상태 어휘와 스키마 결정, option A).
--
-- MySQL's inline CHECK gets an auto-generated name, so rebuild the table exactly like
-- 052 created it, this time with the 5-value status list AND preserving the
-- source_root/source_root_kind columns 069 added afterward.
--
-- Rebuild order matches the SQLite counterpart: build the replacement under a temporary
-- name, copy, DROP the original, then rename into place. Renaming the *original* out of
-- the way instead would leave any real FOREIGN KEY on test_runs pointing at the throwaway
-- backup name (MySQL does not update referencing FK definitions while FOREIGN_KEY_CHECKS
-- is 0) — which is exactly how the first revision of the SQLite file broke
-- test_run_cases. 052 only declares an inline column-level REFERENCES on
-- test_run_cases.run_id, which MySQL parses and ignores, so no MySQL database was
-- actually affected; the order is kept identical so the hazard cannot reappear.

CREATE TABLE test_runs_new (
    run_id       VARCHAR(191) PRIMARY KEY,
    doc_id       VARCHAR(191) NOT NULL REFERENCES documents(doc_id) ON DELETE CASCADE,
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
    tsr_doc_id   VARCHAR(191) REFERENCES documents(doc_id) ON DELETE SET NULL,
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

CREATE INDEX idx_test_runs_doc
    ON test_runs(doc_id, created_at DESC);

SET FOREIGN_KEY_CHECKS=1;
