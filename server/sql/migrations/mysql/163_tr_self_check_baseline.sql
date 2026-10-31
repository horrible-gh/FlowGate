-- 163_tr_self_check_baseline.sql
-- flowgate.v02.0114 T#3 (D 0006 3.2-3.4, 7.2): baseline reuse evidence.
--
-- tr_self_check_runs, three new columns. All NULL on existing rows, nothing is backfilled, so a
-- run made before this migration has no evidence and can never be a reusable baseline (D 3.4-7).
--   result_summary        JSON, the summary of the pytest result block read from a normally
--                         completed run's stdout tail (status none / incomplete / mismatch /
--                         complete, counts, failures, item definition evidence). Untrusted input:
--                         it never changes status, exit_code or timed_out.
--   environment_evidence  JSON, the block's environment evidence (categories a-e).
--   host_node_key         instance_registry.node_key() of the admitting server, copied when the
--                         run row is created (category f, and the per-host sum of step 4).
--
-- tr_self_check_anchors: the pre-change basis of one work unit (a TR and its rework), written
-- once at the common token issue point (token_service.issue) and never rewritten.
--   state        recorded (base_commit set) or none (no basis: dirty tree, probe failure, ...).
--   open_key     sha256(project NUL group NUL prev doc_ref) while the anchor belongs to an
--                unregistered TR(new) draft, NULL once linked. UNIQUE: concurrent issues of one
--                draft unit leave one row ("create only when absent").
--   tr_doc_id    the TR the anchor belongs to once registered. UNIQUE: one anchor per TR.
-- tr_self_check_anchor_tokens: which token was issued against which anchor (token_id PK).
-- No FK on either table: anchors are evidence and outlive tokens, a token issue that cannot
-- write them still succeeds (D 3.3-5).
ALTER TABLE tr_self_check_runs ADD COLUMN result_summary MEDIUMTEXT NULL;
ALTER TABLE tr_self_check_runs ADD COLUMN environment_evidence MEDIUMTEXT NULL;
ALTER TABLE tr_self_check_runs ADD COLUMN host_node_key VARCHAR(191) NULL;

CREATE TABLE tr_self_check_anchors (
    anchor_id VARCHAR(64) PRIMARY KEY,
    project_id VARCHAR(191) NOT NULL,
    group_id VARCHAR(191) NOT NULL,
    prev_doc_ref VARCHAR(191) NULL,
    open_key CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NULL UNIQUE CHECK (open_key IS NULL OR (CHAR_LENGTH(open_key) = 64 AND open_key REGEXP '^[0-9a-f]{64}$')),
    tr_doc_id VARCHAR(191) NULL UNIQUE,
    state VARCHAR(16) NOT NULL CHECK (state IN ('recorded', 'none')),
    base_commit VARCHAR(64) NULL,
    reason VARCHAR(191) NULL,
    first_token_id VARCHAR(191) NULL,
    created_at VARCHAR(40) NOT NULL,
    linked_at VARCHAR(40) NULL,
    CHECK ((state = 'recorded' AND base_commit IS NOT NULL) OR (state = 'none' AND base_commit IS NULL))
);

CREATE INDEX idx_selfcheck_anchor_group ON tr_self_check_anchors (project_id, group_id);

CREATE TABLE tr_self_check_anchor_tokens (
    token_id VARCHAR(191) PRIMARY KEY,
    anchor_id VARCHAR(64) NOT NULL,
    created_at VARCHAR(40) NOT NULL
);

CREATE INDEX idx_selfcheck_anchor_tokens_anchor ON tr_self_check_anchor_tokens (anchor_id);
