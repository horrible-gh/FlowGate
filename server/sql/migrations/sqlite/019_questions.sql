-- T306 Q T-DB table migration
-- Basis: DB003 (Q schema design), R017 (Q asynchronous question channel)
-- 1Q N-questions N-answers model (question_items table separated)
-- A (answer) has no numbering — only the internal ID (PK) is used
-- workflow_sequences table not included (Q is independent from sequences)

BEGIN;

-- ─────────────────────────────────────────────────────────────────────────
-- 1. questions — Q records (1Q)
-- ─────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS questions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    q_id        TEXT    NOT NULL UNIQUE,
    -- Numbering format: Q0001, Q0002, ...

    project_id  TEXT    REFERENCES projects(project_id) ON DELETE SET NULL,
    -- For project-specific Q lookups (NULL = global)

    title       TEXT    NOT NULL,
    -- Overall Q title/summary

    created_by  TEXT    NOT NULL REFERENCES users(user_id),
    -- Worker who registered the question

    pm_id       TEXT    REFERENCES users(user_id),
    -- Assigned PM (NULL = unassigned)

    related_doc TEXT,
    -- Related document ID (T/D/P, etc., informational only)

    status      TEXT    NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'done')),
    -- pending: at least one unanswered question exists, done: all questions answered

    created_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_questions_q_id
    ON questions (q_id);

-- Filter by status (unanswered Q inbox)
CREATE INDEX IF NOT EXISTS idx_questions_status
    ON questions (status)
    WHERE status = 'pending';

-- Project-specific Q list lookup
CREATE INDEX IF NOT EXISTS idx_questions_project_id
    ON questions (project_id, status, created_at);

-- Q list by worker
CREATE INDEX IF NOT EXISTS idx_questions_created_by
    ON questions (created_by, created_at);

-- Assigned Q list by PM
CREATE INDEX IF NOT EXISTS idx_questions_pm_id
    ON questions (pm_id, status);

-- ─────────────────────────────────────────────────────────────────────────
-- 2. question_items — individual questions within a Q (N questions)
-- ─────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS question_items (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    question_id INTEGER NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
    -- FK to the questions table (auto-deleted when the Q is deleted)

    seq         INTEGER NOT NULL,
    -- Question order within the Q (1-based)

    body        TEXT    NOT NULL,
    -- Question body (Markdown)

    answer_count INTEGER NOT NULL DEFAULT 0,
    -- Number of answers for this question (0 = unanswered, for quick filtering)

    created_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),

    UNIQUE (question_id, seq)
);

-- Look up question lists by Q (sorted by seq)
CREATE INDEX IF NOT EXISTS idx_question_items_question_id
    ON question_items (question_id, seq);

-- Optimize unanswered-question filtering (answer_count = 0)
CREATE INDEX IF NOT EXISTS idx_question_items_unanswered
    ON question_items (question_id, answer_count)
    WHERE answer_count = 0;

-- ─────────────────────────────────────────────────────────────────────────
-- 3. answers — answers per question (N answers, no numbering)
-- ─────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS answers (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    -- Internal ID (no numbering — no need for external exposure)

    question_item_id INTEGER NOT NULL REFERENCES question_items(id) ON DELETE CASCADE,
    -- question_items FK (auto-deleted when the question is deleted)

    body        TEXT    NOT NULL,
    -- Answer body (Markdown)

    answered_by TEXT    NOT NULL REFERENCES users(user_id),
    -- Answer author (PM)

    is_accepted INTEGER NOT NULL DEFAULT 0,
    -- Whether the worker has checked/accepted it (0=unchecked, 1=checked)

    created_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

-- Look up answer lists by question
CREATE INDEX IF NOT EXISTS idx_answers_question_item_id
    ON answers (question_item_id, created_at);

-- Lookup by answer author
CREATE INDEX IF NOT EXISTS idx_answers_answered_by
    ON answers (answered_by, created_at);

COMMIT;
