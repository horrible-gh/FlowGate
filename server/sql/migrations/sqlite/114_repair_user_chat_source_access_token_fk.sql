-- 114_repair_user_chat_source_access_token_fk.sql
-- flowgate.default.0568 TR0005 rev2 (rejection 2): repair a second table SQLite's
-- automatic foreign-key-reference rewrite corrupted -- not a rare out-of-order-merge edge
-- case like 110_repair_tokens_after_base_dirty_rebuild.sql's own bug, but a deterministic
-- hit on every SQLite deployment that has applied 110 (already merged to main), regardless
-- of merge order. Proven both ways by test_user_chat_source_access_114_repair_0568.py.
--
-- 108_user_chat_source_access.sql (flowgate.default.0515) created
-- `user_chat_source_access.one_shot_token_id TEXT REFERENCES tokens(token_id)`. Any later
-- `ALTER TABLE tokens RENAME TO <tmp>` makes SQLite do what it always does on a table
-- rename: rewrite every *other* table's FOREIGN KEY clause that names the old table, in
-- place, to the new name. `108_user_chat_source_access.sql` unconditionally sorts before
-- `109_tokens_source_access.sql` and `110_repair_tokens_after_base_dirty_rebuild.sql`
-- (alphabetical filename order), so `user_chat_source_access` always exists by the time
-- either of those two renames `tokens` -- 108_tokens_resolve_base_dirty_scope.sql's own
-- rename when it merges out of order (sqloader tracks only filename, not merge time, same
-- root cause 110's own header documents for `tokens`), and unconditionally,
-- 110_repair_tokens_after_base_dirty_rebuild.sql's rename, since 110 always runs after
-- 108_user_chat_source_access.sql on every ledger. Either rename fires the rewrite against
-- `user_chat_source_access`, silently changing its stored schema text to
-- `REFERENCES "tokens_before_base_dirty_scope"(token_id)` or
-- `REFERENCES "tokens_before_110_repair"(token_id)` depending on which rename hit it first
-- -- whichever ran first "wins", because a later rename to `tokens` no longer matches a
-- reference that already points elsewhere. Each such migration then drops its own
-- intermediate table once the rebuilt `tokens` exists, but nothing rewrites the reference
-- back -- the rename only follows the table being renamed, never a table it is referenced
-- *by*. The result is a permanently dangling FK target that 110 did not touch (110 only
-- rebuilt `tokens`, not the tables pointing at it).
--
-- Symptom confirmed live (flowgate.default.0568 TR0005 rejection 2, 2026-09-19, dev
-- database copy, ledger: 108_user_chat_source_access+109 at 2026-09-08T12:46:54,
-- 108_tokens_resolve_base_dirty_scope at 2026-09-08T21:01:24, 110 at 2026-09-10T13:30:45):
-- every write to user_chat_source_access -- i.e. every Quick Mode PATCH /me/chat-settings
-- that touches source_access_mode, the feature's actual save path -- fails at statement
-- compile time with sqlite3.OperationalError: no such table: main.tokens_before_base_dirty_scope,
-- because SQLite must resolve a table's FK target to compile any INSERT/UPDATE against it,
-- independent of whether the column value being written is NULL.
--
-- Same repair shape as 110: rebuild the table with the same columns and the corrected FK.
PRAGMA foreign_keys=OFF;
BEGIN;
ALTER TABLE user_chat_source_access RENAME TO user_chat_source_access_before_114_repair;
CREATE TABLE user_chat_source_access (
    user_id             TEXT NOT NULL PRIMARY KEY
                             REFERENCES users(user_id) ON DELETE CASCADE,
    source_access_mode  TEXT NOT NULL
                             CHECK (source_access_mode IN ('read_only', 'edit', 'edit_once')),
    one_shot_token_id   TEXT
                             REFERENCES tokens(token_id) ON DELETE RESTRICT,
    one_shot_claimed_at TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    CHECK ((one_shot_token_id IS NULL) = (one_shot_claimed_at IS NULL))
);
INSERT INTO user_chat_source_access (user_id, source_access_mode, one_shot_token_id,
    one_shot_claimed_at, created_at, updated_at)
SELECT user_id, source_access_mode, one_shot_token_id,
    one_shot_claimed_at, created_at, updated_at
FROM user_chat_source_access_before_114_repair;
DROP TABLE user_chat_source_access_before_114_repair;
CREATE INDEX idx_ucsa_one_shot_token_id
    ON user_chat_source_access(one_shot_token_id);
COMMIT;
PRAGMA foreign_keys=ON;
