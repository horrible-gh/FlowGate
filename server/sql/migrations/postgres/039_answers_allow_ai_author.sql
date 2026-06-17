-- 039 Q/A/V 개편 ② — answers 를 사람·AI 양방향으로 (테이블 재작성).
-- Basis: group 0022 DB0006 §3.3/§4.2, L0007 §3.3
-- answered_by NOT NULL → users 제약을 풀어야 AI 답변(사람 user 행 없음)을 담을 수 있다.
-- SQLite 는 NOT NULL 완화에 ALTER 가 없어 표준 재작성(create-copy-drop-rename).
-- 개명: answered_by → author_id(NULL 허용) + author_kind. answers 는 리프 테이블이라
-- 재작성이 안전(하위 CASCADE 참조 없음). 소비자(queries.json/db/services/routes)는 동시 착지.

CREATE TABLE answers_new (
    id               SERIAL PRIMARY KEY,
    question_item_id INTEGER NOT NULL REFERENCES question_items(id) ON DELETE CASCADE,
    body        TEXT    NOT NULL,
    author_kind TEXT    NOT NULL DEFAULT 'human'
                    CHECK (author_kind IN ('human','ai')),
    author_id   TEXT    REFERENCES users(user_id),      -- NULL 허용(author_kind='ai')
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
