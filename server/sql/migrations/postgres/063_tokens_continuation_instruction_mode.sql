-- 063_tokens_continuation_instruction_mode.sql
-- Persist how continuous work handles N/T instruction steps.

ALTER TABLE tokens ADD COLUMN continuation_instruction_mode TEXT;
