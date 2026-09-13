-- 107_ai_invoke_document_review_loop_review_stalled.sql
-- flowgate.default.0486 T0029 item 2 (NR0028 F2): the stall detector added by T0021 stores
-- stop_reason='review_stalled', a value the CHECK inherited from 106 does not list, so the
-- checkpoint UPDATE raised IntegrityError and rolled the whole transaction back -- taking
-- the automatic document rejection written in the same transaction with it. SQLite cannot
-- alter a CHECK in place, so preserve the current 106 shape, data, foreign keys and indexes
-- while widening stop_reason by exactly one value.
PRAGMA foreign_keys = OFF;
BEGIN;
CREATE TABLE ai_invoke_document_review_loops_new (
 run_id TEXT PRIMARY KEY,
 group_id TEXT NOT NULL REFERENCES groups(group_id) ON DELETE CASCADE,
 doc_ref TEXT NOT NULL REFERENCES documents(doc_id) ON DELETE CASCADE,
 review_count INTEGER NOT NULL CHECK (review_count IN (-1,1,2,3)),
 reviewer_provider_id TEXT NOT NULL REFERENCES ai_providers(provider_id) ON DELETE RESTRICT,
 review_criteria TEXT NOT NULL CHECK (review_criteria IN ('document_type_default','last_rejection_only')),
 rework_provider_id TEXT NOT NULL REFERENCES ai_providers(provider_id) ON DELETE RESTRICT,
 rework_timeout_sec INTEGER NOT NULL CHECK (rework_timeout_sec IN (1800,3600,7200)),
 rework_message TEXT NOT NULL DEFAULT '',
 failure_restart_max_attempts INTEGER NOT NULL CHECK (failure_restart_max_attempts IN (-1,0,1,2)),
 total_timeout_sec INTEGER NOT NULL CHECK (total_timeout_sec IN (3600,7200,14400)),
 review_baseline_id INTEGER NOT NULL DEFAULT 0 CHECK (review_baseline_id >= 0),
 baseline_revision_no INTEGER NOT NULL CHECK (baseline_revision_no >= 0),
 starts_with_rework INTEGER NOT NULL DEFAULT 0 CHECK (starts_with_rework IN (0,1)),
 started_at TEXT NOT NULL, deadline_at TEXT NOT NULL,
 round_no INTEGER NOT NULL DEFAULT 1 CHECK (round_no >= 1),
 current_stage TEXT NOT NULL CHECK (current_stage IN ('rework','review','stopped')),
 stop_reason TEXT CHECK (stop_reason IS NULL OR stop_reason IN ('review_passed','review_count_exhausted','retry_exhausted','total_timeout','review_verdict_hold','restart_orphaned','review_stalled')),
 stop_detail TEXT,
 last_hop_kind TEXT CHECK (last_hop_kind IS NULL OR last_hop_kind IN ('rework','review')),
 last_hop_outcome TEXT CHECK (last_hop_outcome IS NULL OR last_hop_outcome IN ('succeeded','failed')),
 attempts_used INTEGER NOT NULL DEFAULT 0 CHECK (attempts_used >= 0),
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL, card_dismissed_at TEXT,
 CHECK ((current_stage <> 'stopped' AND stop_reason IS NULL AND stop_detail IS NULL) OR (current_stage = 'stopped' AND stop_reason IS NOT NULL)),
 CHECK ((stop_reason IS NULL) OR (stop_reason='review_passed' AND stop_detail IS NULL) OR (stop_reason<>'review_passed' AND stop_detail IS NOT NULL)),
 CHECK ((last_hop_kind IS NULL) = (last_hop_outcome IS NULL))
);
INSERT INTO ai_invoke_document_review_loops_new SELECT * FROM ai_invoke_document_review_loops;
DROP TABLE ai_invoke_document_review_loops;
ALTER TABLE ai_invoke_document_review_loops_new RENAME TO ai_invoke_document_review_loops;
CREATE INDEX idx_aidrl_group_updated ON ai_invoke_document_review_loops(group_id, updated_at);
CREATE INDEX idx_aidrl_doc_updated ON ai_invoke_document_review_loops(doc_ref, updated_at);
COMMIT;
PRAGMA foreign_keys = ON;
