-- 039 Q/A/V revamp 2 - allow answers from both humans and AI (table rewrite).
-- Basis: group 0022 DB0006 §3.3/§4.2, L0007 §3.3
-- Relax answered_by NOT NULL and the users constraint so AI answers can be stored without a human user row.
-- SQLite cannot relax NOT NULL with ALTER, so use the standard create-copy-drop-rename rewrite.
-- Rename answered_by to nullable author_id plus author_kind. answers is a leaf table,
-- so the rewrite is safe: no downstream CASCADE references. Consumers land with this migration.

CREATE TABLE answers_new (
    id               SERIAL PRIMARY KEY,
    question_item_id INTEGER NOT NULL REFERENCES question_items(id) ON DELETE CASCADE,
    body        TEXT    NOT NULL,
    author_kind TEXT    NOT NULL DEFAULT 'human'
                    CHECK (author_kind IN ('human','ai')),
    author_id   TEXT    REFERENCES users(user_id),      -- NULL allowed (author_kind='ai')
    is_accepted INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT    NOT NULL,
    updated_at  TEXT    NOT NULL
);
INSERT INTO answers_new (id, question_item_id, body, author_kind, author_id,
                         is_accepted, created_at, updated_at)
    SELECT id, question_item_id, body, 'human', answered_by,
           is_accepted, created_at, updated_at FROM answers;
-- [pg-fk-rebuild] preserve inbound FOREIGN KEYs across the drop+recreate of "answers"
DO $$
DECLARE _stmt text;
BEGIN
    CREATE TEMP TABLE _fk_rb_answers ON COMMIT DROP AS
            SELECT 'ALTER TABLE ' || quote_ident(n.nspname) || '.' || quote_ident(c.relname)
                   || ' ADD CONSTRAINT ' || quote_ident(con.conname) || ' ' || pg_get_constraintdef(con.oid) AS stmt
            FROM pg_constraint con
            JOIN pg_class c ON c.oid = con.conrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE con.contype = 'f' AND con.confrelid = to_regclass('answers')
              AND con.conrelid <> con.confrelid;
    FOR _stmt IN
        SELECT 'ALTER TABLE ' || quote_ident(n.nspname) || '.' || quote_ident(c.relname)
               || ' DROP CONSTRAINT ' || quote_ident(con.conname)
        FROM pg_constraint con
        JOIN pg_class c ON c.oid = con.conrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE con.contype = 'f' AND con.confrelid = to_regclass('answers')
          AND con.conrelid <> con.confrelid
    LOOP
        EXECUTE _stmt;
    END LOOP;
END $$;
DROP TABLE answers;
ALTER TABLE answers_new RENAME TO answers;
CREATE INDEX IF NOT EXISTS idx_answers_question_item_id
    ON answers(question_item_id, created_at);
CREATE INDEX IF NOT EXISTS idx_answers_author
    ON answers(author_id, created_at);

-- [pg-fk-rebuild] restore inbound FOREIGN KEYs for "answers"
DO $$
DECLARE _stmt text;
BEGIN
    IF to_regclass('pg_temp._fk_rb_answers') IS NOT NULL THEN
        FOR _stmt IN SELECT stmt FROM _fk_rb_answers LOOP
            EXECUTE _stmt;
        END LOOP;
    END IF;
END $$;
