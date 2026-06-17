SET FOREIGN_KEY_CHECKS=0;
-- 039 Q/A/V revamp 2 - allow answers from both humans and AI (table rewrite).
-- Basis: group 0022 DB0006 §3.3/§4.2, L0007 §3.3
-- Relax answered_by NOT NULL and the users constraint so AI answers can be stored without a human user row.
-- SQLite cannot relax NOT NULL with ALTER, so use the standard create-copy-drop-rename rewrite.
-- Rename answered_by to nullable author_id plus author_kind. answers is a leaf table,
-- so the rewrite is safe: no downstream CASCADE references. Consumers land with this migration.

CREATE TABLE answers_new (
    id               INTEGER PRIMARY KEY AUTO_INCREMENT,
    question_item_id INTEGER NOT NULL REFERENCES question_items(id) ON DELETE CASCADE,
    body        TEXT    NOT NULL,
    author_kind TEXT    NOT NULL DEFAULT 'human'
                    CHECK (author_kind IN ('human','ai')),
    author_id   VARCHAR(191)    REFERENCES users(user_id),      -- NULL allowed (author_kind='ai')
    is_accepted INTEGER NOT NULL DEFAULT 0,
    created_at  VARCHAR(191)    NOT NULL,
    updated_at  TEXT    NOT NULL
);
INSERT INTO answers_new (id, question_item_id, body, author_kind, author_id,
                         is_accepted, created_at, updated_at)
    SELECT id, question_item_id, body, 'human', answered_by,
           is_accepted, created_at, updated_at FROM answers;
DROP TABLE answers;
ALTER TABLE answers_new RENAME TO answers;
CREATE INDEX IF NOT EXISTS idx_answers_question_item_id
    ON answers(question_item_id, created_at);
CREATE INDEX IF NOT EXISTS idx_answers_author
    ON answers(author_id, created_at);
SET FOREIGN_KEY_CHECKS=1;
