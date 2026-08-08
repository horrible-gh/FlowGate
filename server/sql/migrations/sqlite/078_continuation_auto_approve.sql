-- 078_continuation_auto_approve.sql
-- flowgate.default.0352 T0004 (R0001 / NR0003): per-item_seq N/T auto-approve selection for
-- the [지시서 작성 후 진행] (ai_direct) continuous chain, plus the paused-chain mode/set columns
-- needed to fix the pause->resume mode-loss bug (a chain started with ai_direct lost the mode
-- on resume and silently fell back to auto_approved). Additive only, all nullable: NULL/absent
-- means "no selection" for the item_seq set, and NULL mode on a paused row means "unknown, fall
-- back to auto_approved" — both preserve today's behavior for pre-migration rows.

BEGIN;

ALTER TABLE tokens ADD COLUMN continuation_auto_approve_item_seqs TEXT;

-- ai_invoke_paused_chains never stored the instruction mode at all — that omission is the root
-- of the resume bug (T0004 §3.6): resume_chain read nothing back and hard-coded "auto_approved".
ALTER TABLE ai_invoke_paused_chains ADD COLUMN continuation_instruction_mode TEXT;
ALTER TABLE ai_invoke_paused_chains ADD COLUMN continuation_auto_approve_item_seqs TEXT;

COMMIT;
