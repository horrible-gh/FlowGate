-- 0555 A11: durable clean-finalize approval retry evidence.
-- The JSON snapshot is consumed atomically with the AC/root approval transaction.
ALTER TABLE group_git_state ADD COLUMN final_approval_retry TEXT;