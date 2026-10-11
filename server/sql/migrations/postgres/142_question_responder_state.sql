-- 142_question_responder_state.sql
-- flowgate.default.0661 T0004 (0003-NR F2/F3/F5/F6): durable state of the automatic Q responder
-- and the requested-provider half of an AI answer's provenance.
--
-- question_items
--   responder_state                 NULL = no in-app AI responder has touched this item yet
--                                   'dispatched'    an AI responder run was started for it
--                                   'answered'      an answer landed on it afterwards (AI or human)
--                                   'failed'        technical failure, see responder_error_code
--                                   'user_decision' the responder explicitly handed it to a human
--   responder_run_id                the latest responder run (ai_invoke_runs.run_id; no FK -- a live
--                                   run is not a durable row until it finalizes)
--   responder_requested_provider_id the provider the dispatcher asked for (reviewer/header/default)
--   responder_provider_source       reviewer_override | sequence_reviewer | header | default
--   responder_error_code            no_enabled_provider | provider_unavailable | run_in_progress |
--                                   timeout | cancelled | provider_failed | group_lease_denied |
--                                   no_answer_registered | interrupted | dispatch_error
--   responder_error_message         operator-facing detail (stop_reason / admission message)
--   responder_attempts              responder runs started for this item (any path)
--   responder_updated_at            last responder_state transition
-- answers
--   author_requested_provider_id    the provider that was asked to answer (NULL = no evidence/legacy)
--   author_provider_source          selection source at run start, or 'fallback' when a fallback ran
--   author_fallback_used            0/1, NULL = no evidence
-- Existing rows stay NULL / 0: legacy Q&A predates the responder state machine.
ALTER TABLE question_items ADD COLUMN IF NOT EXISTS responder_state TEXT NULL;
ALTER TABLE question_items ADD COLUMN IF NOT EXISTS responder_run_id TEXT NULL;
ALTER TABLE question_items ADD COLUMN IF NOT EXISTS responder_requested_provider_id TEXT NULL;
ALTER TABLE question_items ADD COLUMN IF NOT EXISTS responder_provider_source TEXT NULL;
ALTER TABLE question_items ADD COLUMN IF NOT EXISTS responder_error_code TEXT NULL;
ALTER TABLE question_items ADD COLUMN IF NOT EXISTS responder_error_message TEXT NULL;
ALTER TABLE question_items ADD COLUMN IF NOT EXISTS responder_attempts INTEGER NOT NULL DEFAULT 0;
ALTER TABLE question_items ADD COLUMN IF NOT EXISTS responder_updated_at TEXT NULL;
ALTER TABLE answers ADD COLUMN IF NOT EXISTS author_requested_provider_id TEXT NULL;
ALTER TABLE answers ADD COLUMN IF NOT EXISTS author_provider_source TEXT NULL;
ALTER TABLE answers ADD COLUMN IF NOT EXISTS author_fallback_used SMALLINT NULL;
CREATE INDEX IF NOT EXISTS idx_question_items_responder_run ON question_items (responder_run_id);
