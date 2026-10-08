-- 137_chat_activity_anchor.sql
-- flowgate.default.0675 T0004 §2-3 / §2-5: a persisted conversation position for chat
-- command requests and run change summaries, decided by the server instead of the
-- screen re-matching against whatever page of turns it currently holds.
--   run_start_seq    conversation head seq when the chat run started (NULL = unknown,
--                    i.e. every row written before this migration)
--   anchor_seq       the turn the row is placed against (0 = before the first turn)
--   anchor_position  before / after that turn
--   anchor_state     reply / run_start / ambiguous / unresolved (NULL = not yet decided)
-- Additive only. Existing rows are backfilled at startup by
-- chat_activity_anchor_service.backfill(), which logs before/after counts per state,
-- using the same rule as live writes (one AI turn of the run -> reply; none or several
-- -> unresolved / ambiguous; created_at is never used to guess).

ALTER TABLE chat_command_requests ADD COLUMN run_start_seq INTEGER NULL;
ALTER TABLE chat_command_requests ADD COLUMN anchor_seq INTEGER NULL;
ALTER TABLE chat_command_requests ADD COLUMN anchor_position VARCHAR(8) NULL;
ALTER TABLE chat_command_requests ADD COLUMN anchor_state VARCHAR(16) NULL;

ALTER TABLE ai_run_source_changes ADD COLUMN run_start_seq INTEGER NULL;
ALTER TABLE ai_run_source_changes ADD COLUMN anchor_seq INTEGER NULL;
ALTER TABLE ai_run_source_changes ADD COLUMN anchor_position VARCHAR(8) NULL;
ALTER TABLE ai_run_source_changes ADD COLUMN anchor_state VARCHAR(16) NULL;

CREATE INDEX idx_chat_cmd_anchor ON chat_command_requests (doc_id, anchor_seq);
CREATE INDEX idx_run_changes_anchor ON ai_run_source_changes (doc_id, anchor_seq);

ALTER TABLE chat_command_requests ADD CONSTRAINT chk_chat_cmd_anchor_position
    CHECK (anchor_position IS NULL OR anchor_position IN ('before', 'after'));
ALTER TABLE chat_command_requests ADD CONSTRAINT chk_chat_cmd_anchor_state
    CHECK (anchor_state IS NULL OR anchor_state IN ('reply', 'run_start', 'ambiguous', 'unresolved'));
ALTER TABLE ai_run_source_changes ADD CONSTRAINT chk_run_changes_anchor_position
    CHECK (anchor_position IS NULL OR anchor_position IN ('before', 'after'));
ALTER TABLE ai_run_source_changes ADD CONSTRAINT chk_run_changes_anchor_state
    CHECK (anchor_state IS NULL OR anchor_state IN ('reply', 'run_start', 'ambiguous', 'unresolved'));
