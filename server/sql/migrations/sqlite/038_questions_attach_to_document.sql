-- 038 Q/A/V revamp 2 - attach the questions container to documents(doc_id).
-- Basis: group 0022 DB0006 §3.3/§4.1, L0007 §3.1
-- (a) questions.doc_id FK + UNIQUE(doc_id) means one container per document.
-- (b) question_items.title / asker_kind (title and asker type).
-- (c) Reserved system user 'u-system' for AI question containers; satisfies created_by FK and NOT NULL.
-- Backfill: normalize legacy project-scoped questions to doc_id with a many-to-one guard; promote one representative row.
--
-- Re-run safety comes from migration tracking plus whole-file transaction rollback, as in 035.
-- SQLite has no IF NOT EXISTS for ALTER TABLE ADD COLUMN, so this is not statement-level idempotent.

BEGIN;

-- (a) questions: add the document FK without a default, which SQLite allows for ADD COLUMN.
ALTER TABLE questions ADD COLUMN doc_id TEXT
    REFERENCES documents(doc_id) ON DELETE CASCADE;

-- (b) question_items: title and asker type.
ALTER TABLE question_items ADD COLUMN title TEXT;
ALTER TABLE question_items ADD COLUMN asker_kind TEXT NOT NULL DEFAULT 'human'
    CHECK (asker_kind IN ('human','ai'));

-- (c) Reserved system user for AI question containers; login disabled with is_active=0.
INSERT OR IGNORE INTO users
    (user_id, username, email, password, is_active, is_admin,
     first_login_required, created_at, updated_at)
VALUES
    ('u-system', 'system', 'system@flowgate.local', '!', 0, 0, 0,
     datetime('now'), datetime('now'));

-- Backfill (many-to-one guard) --------------------------------------------
-- Legacy questions were independently numbered per project, so multiple rows
-- can point at the same related_doc. If every row becomes doc_id=q_id=related_doc,
-- both the global q_id UNIQUE(idx_questions_q_id) and the new ux_questions_doc
-- partial UNIQUE index can be violated, failing the whole transaction. Promote one representative row per document.

-- 1) Rows where q_id is already a real doc_id: fill doc_id without resetting q_id.
--    q_id is globally UNIQUE, so there can be at most one row per document and no collision.
UPDATE questions
   SET doc_id = q_id
 WHERE doc_id IS NULL
   AND q_id IN (SELECT doc_id FROM documents);

-- 2) Non-normalized rows whose related_doc is a real document and has no container: promote one representative row.
--    The representative is MIN(id). Remaining many-to-one rows stay doc_id IS NULL and remain hidden per section 6.
UPDATE questions
   SET doc_id = related_doc,
       q_id   = related_doc
 WHERE doc_id IS NULL
   AND related_doc IN (SELECT doc_id FROM documents)
   AND related_doc NOT IN (SELECT doc_id FROM questions WHERE doc_id IS NOT NULL)
   AND id = (SELECT MIN(q2.id) FROM questions q2
              WHERE q2.doc_id IS NULL AND q2.related_doc = questions.related_doc);

-- One container per document; after backfill, NULL and remaining global rows are excluded by the partial index.
CREATE UNIQUE INDEX IF NOT EXISTS ux_questions_doc
    ON questions(doc_id) WHERE doc_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_questions_doc_status
    ON questions(doc_id, status, created_at);

COMMIT;
