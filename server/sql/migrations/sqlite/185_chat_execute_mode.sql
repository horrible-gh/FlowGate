-- 185_chat_execute_mode.sql — flowgate.v02test.0026 (0001-B §7): the chat "execute" mode.
-- Additive only. Two things:
--   tokens.command_access              NULL for every token but a chat token issued in execute mode, which
--                                      carries 'execute': it may run FlowGate commands while its
--                                      source_access stays 'read' (no source or document edit). Fixed at
--                                      issue time like source_access; no backfill.
--   user_chat_source_access.mode       admits 'execute' between read_only and edit.
--
-- SQLite cannot widen a CHECK in place, so user_chat_source_access is rebuilt: every column, every row is
-- kept. Nothing references user_chat_source_access and no trigger names it; the replacement name is
-- cleared first. tokens only gains a column (ADD COLUMN never renames tokens, so no foreign key text
-- is rewritten).
ALTER TABLE tokens ADD COLUMN command_access TEXT CHECK (command_access IN ('execute'));

PRAGMA foreign_keys=OFF;
BEGIN;
DROP TABLE IF EXISTS user_chat_source_access_new;

CREATE TABLE user_chat_source_access_new (
    user_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    group_id TEXT NOT NULL,
    source_access_mode TEXT NOT NULL,
    one_shot_token_id TEXT,
    one_shot_claimed_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CONSTRAINT pk_ucsa_group PRIMARY KEY (user_id, project_id, group_id),
    CONSTRAINT ck_ucsa_group_mode CHECK (source_access_mode IN ('read_only', 'execute', 'edit', 'edit_once')),
    CONSTRAINT ck_ucsa_group_marker CHECK ((one_shot_token_id IS NULL) = (one_shot_claimed_at IS NULL)),
    CONSTRAINT fk_ucsa_group_user FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE,
    CONSTRAINT fk_ucsa_group_token FOREIGN KEY (one_shot_token_id) REFERENCES tokens(token_id) ON DELETE RESTRICT,
    CONSTRAINT fk_ucsa_group_group FOREIGN KEY (group_id) REFERENCES groups(group_id) ON DELETE CASCADE
);

INSERT INTO user_chat_source_access_new (user_id, project_id, group_id, source_access_mode, one_shot_token_id,
                                         one_shot_claimed_at, created_at, updated_at)
SELECT user_id, project_id, group_id, source_access_mode, one_shot_token_id,
       one_shot_claimed_at, created_at, updated_at FROM user_chat_source_access;

DROP TABLE user_chat_source_access;
ALTER TABLE user_chat_source_access_new RENAME TO user_chat_source_access;
CREATE UNIQUE INDEX IF NOT EXISTS idx_ucsa_group_one_shot_token_id
    ON user_chat_source_access(one_shot_token_id);
COMMIT;
PRAGMA foreign_keys=ON;

-- ── DOWN (rollback) ───────────────────────────────────────────────────────────
-- Not advised. Default rollback = revert the code only: an older server reads 'execute' as read_only
-- (resolve_source_access_mode's fail-closed domain check) and never reads tokens.command_access.
-- ── END DOWN ──────────────────────────────────────────────────────────────────
