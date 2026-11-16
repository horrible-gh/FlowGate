-- 192_ai_invoke_document_review_loop_review_count_range.sql (mysql)
-- flowgate.v02test.0027 0007-T (TR0008 rev1 rejection): the loop's review_count CHECK still froze (-1,1,2,3)
-- although the allowed count is the policy's (-1, 1..max, max 1..30): a card's result review loop asking for 5
-- under max 5 raised IntegrityError. Widen it to the graph_stage_assignments.review_rounds rule (164):
-- review_count = -1 OR review_count >= 1. MySQL's unnamed CHECK name is not portable, so rebuild the current
-- 107 shape (data, foreign keys, indexes kept) with only that CHECK changed, as 107 did.
-- DOWN: rebuild with CHECK (review_count IN (-1,1,2,3)) after rows above 3 are gone (not automatic).
CREATE TABLE ai_invoke_document_review_loops_new (
 run_id VARCHAR(191) PRIMARY KEY, group_id VARCHAR(191) NOT NULL, doc_ref VARCHAR(191) NOT NULL,
 review_count INTEGER NOT NULL CHECK (review_count = -1 OR review_count >= 1), reviewer_provider_id VARCHAR(191) NOT NULL,
 review_criteria TEXT NOT NULL CHECK (review_criteria IN ('document_type_default','last_rejection_only')), rework_provider_id VARCHAR(191) NOT NULL,
 rework_timeout_sec INTEGER NOT NULL CHECK (rework_timeout_sec IN (1800,3600,7200)), rework_message TEXT NOT NULL,
 failure_restart_max_attempts INTEGER NOT NULL CHECK (failure_restart_max_attempts IN (-1,0,1,2)), total_timeout_sec INTEGER NOT NULL CHECK (total_timeout_sec IN (3600,7200,14400)),
 review_baseline_id BIGINT NOT NULL DEFAULT 0 CHECK (review_baseline_id >= 0), baseline_revision_no INTEGER NOT NULL CHECK (baseline_revision_no >= 0), starts_with_rework TINYINT(1) NOT NULL DEFAULT 0 CHECK (starts_with_rework IN (0,1)),
 started_at TEXT NOT NULL, deadline_at TEXT NOT NULL, round_no INTEGER NOT NULL DEFAULT 1 CHECK (round_no >= 1), current_stage VARCHAR(16) NOT NULL CHECK (current_stage IN ('rework','review','stopped')),
 stop_reason VARCHAR(32) NULL CHECK (stop_reason IS NULL OR stop_reason IN ('review_passed','review_count_exhausted','retry_exhausted','total_timeout','review_verdict_hold','restart_orphaned','review_stalled')), stop_detail TEXT NULL,
 last_hop_kind VARCHAR(16) NULL CHECK (last_hop_kind IS NULL OR last_hop_kind IN ('rework','review')), last_hop_outcome VARCHAR(16) NULL CHECK (last_hop_outcome IS NULL OR last_hop_outcome IN ('succeeded','failed')),
 attempts_used INTEGER NOT NULL DEFAULT 0 CHECK (attempts_used >= 0), created_at TEXT NOT NULL, updated_at TEXT NOT NULL, card_dismissed_at VARCHAR(64) NULL,
 CONSTRAINT fk_aidrl_group_count FOREIGN KEY(group_id) REFERENCES groups(group_id) ON DELETE CASCADE,
 CONSTRAINT fk_aidrl_doc_count FOREIGN KEY(doc_ref) REFERENCES documents(doc_id) ON DELETE CASCADE,
 CONSTRAINT fk_aidrl_reviewer_count FOREIGN KEY(reviewer_provider_id) REFERENCES ai_providers(provider_id) ON DELETE RESTRICT,
 CONSTRAINT fk_aidrl_rework_count FOREIGN KEY(rework_provider_id) REFERENCES ai_providers(provider_id) ON DELETE RESTRICT,
 CHECK ((current_stage <> 'stopped' AND stop_reason IS NULL AND stop_detail IS NULL) OR (current_stage = 'stopped' AND stop_reason IS NOT NULL)),
 CHECK ((stop_reason IS NULL) OR (stop_reason='review_passed' AND stop_detail IS NULL) OR (stop_reason<>'review_passed' AND stop_detail IS NOT NULL)), CHECK ((last_hop_kind IS NULL) = (last_hop_outcome IS NULL))
);
INSERT INTO ai_invoke_document_review_loops_new SELECT * FROM ai_invoke_document_review_loops;
DROP TABLE ai_invoke_document_review_loops;
RENAME TABLE ai_invoke_document_review_loops_new TO ai_invoke_document_review_loops;
CREATE INDEX idx_aidrl_group_updated ON ai_invoke_document_review_loops(group_id, updated_at(32));
CREATE INDEX idx_aidrl_doc_updated ON ai_invoke_document_review_loops(doc_ref, updated_at(32));
