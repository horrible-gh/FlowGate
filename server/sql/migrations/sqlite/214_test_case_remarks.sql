-- 214_test_case_remarks.sql
-- F81 test card: remarks on a scenario case.
--
-- One row per remark, kept by the TS document and the case id it was written on.
--   author_kind   -- ai | human (who wrote it); author_name is the name shown.
--   phase         -- case_write | test_run (the AI stage the remark belongs to).
--   state         -- open | resolved. A resolved remark can be opened again.
--   ai_reply      -- the AI's one-line answer when it handled the remark.
-- Additive: nothing existing reads or writes this table.

CREATE TABLE IF NOT EXISTS test_case_remarks (
    remark_id VARCHAR(64) PRIMARY KEY,
    doc_id VARCHAR(191) NOT NULL,
    case_id VARCHAR(191) NOT NULL,
    author_kind VARCHAR(16) NOT NULL,
    author_name VARCHAR(200) NOT NULL DEFAULT '',
    phase VARCHAR(16) NOT NULL,
    state VARCHAR(16) NOT NULL DEFAULT 'open',
    body TEXT NOT NULL,
    ai_reply TEXT NULL,
    created_at VARCHAR(64) NOT NULL,
    updated_at VARCHAR(64) NOT NULL,
    resolved_at VARCHAR(64) NULL
);
CREATE INDEX IF NOT EXISTS idx_test_case_remarks_doc ON test_case_remarks (doc_id, case_id);
