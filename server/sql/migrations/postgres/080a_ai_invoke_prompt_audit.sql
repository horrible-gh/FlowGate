-- 080a_ai_invoke_prompt_audit.sql
-- flowgate.default.0406 (B0001 -> M0019 -> N0020 -> NR0021 -> T0022 tasks 3 and 5):
-- Close the audit gap that prevented investigating missing messages in unattended continuous work.
--
-- NR0021 §8 established that the continuous-run dialog's session [전달멘트] common value, step override,
-- and final worker prompt are not retained anywhere. Neither tokens nor ai_invoke_runs
-- has those columns. Thus whether text the user remembered entering actually reached
-- the prompt could neither be reproduced nor disproved. For the same reason, hops such as T0013/T0017,
-- where the server wrote and approved N/T without attaching an AI worker, were indistinguishable
-- afterward from a missing step.
--
-- Add two groups of fields, all on ai_invoke_runs and queryable by run_id.
--   1) Who performed this hop: worker_document_type, auto_handled_item_seqs,
--      continuation_instruction_mode_{requested,normalized,fallback_applied}.
--      Crucially, retain both the requested and normalized values: a user choosing auto_approved
--      differs from an entry point omitting the mode and letting the server choose it.
--   2) Whether the message reached the prompt: prompt_message_source (step override / common default /
--      saved sequence note fallback / none), plus length and sha256 of the applied text and final prompt.
--
-- **Do not store the original text.** Lengths, hashes, and decisions suffice to investigate these reports.
-- Retaining originals would introduce information leakage and conflict with the 90-day retention policy.
--
-- Additive only: do not remove or rename existing columns or backfill. Runs completed before
-- this migration read as NULL/0, accurately indicating that these values were not recorded then.

BEGIN;

ALTER TABLE ai_invoke_runs ADD COLUMN IF NOT EXISTS worker_document_type TEXT;
ALTER TABLE ai_invoke_runs ADD COLUMN IF NOT EXISTS continuation_instruction_mode_requested TEXT;
ALTER TABLE ai_invoke_runs ADD COLUMN IF NOT EXISTS continuation_instruction_mode_normalized TEXT;
ALTER TABLE ai_invoke_runs ADD COLUMN IF NOT EXISTS continuation_instruction_mode_fallback_applied SMALLINT NOT NULL DEFAULT 0;
ALTER TABLE ai_invoke_runs ADD COLUMN IF NOT EXISTS auto_handled_item_seqs TEXT;
ALTER TABLE ai_invoke_runs ADD COLUMN IF NOT EXISTS prompt_message_source TEXT;
ALTER TABLE ai_invoke_runs ADD COLUMN IF NOT EXISTS prompt_common_default_applied SMALLINT NOT NULL DEFAULT 0;
ALTER TABLE ai_invoke_runs ADD COLUMN IF NOT EXISTS prompt_user_message_length INTEGER NOT NULL DEFAULT 0;
ALTER TABLE ai_invoke_runs ADD COLUMN IF NOT EXISTS prompt_user_message_sha256 TEXT;
ALTER TABLE ai_invoke_runs ADD COLUMN IF NOT EXISTS prompt_final_length INTEGER NOT NULL DEFAULT 0;
ALTER TABLE ai_invoke_runs ADD COLUMN IF NOT EXISTS prompt_final_sha256 TEXT;

COMMIT;
