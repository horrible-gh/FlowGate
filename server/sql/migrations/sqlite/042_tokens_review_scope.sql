-- Add 'review' to the tokens.action_scope CHECK constraint.
--
-- B0057.0001 / NR0057.0003 / TR0057.0005: request_review() was changed to issue
-- action_scope="review" tokens (so a review mention can no longer double as an edit
-- grant). But the tokens table CHECK from migration 036 only allowed
-- ('new','edit','workflow_decide'), so every review-request INSERT failed the CHECK
-- and the /documents/review-request endpoint returned 500. This expands the CHECK so
-- the issue path the fix relies on actually persists.
--
-- SQLite cannot ALTER a CHECK constraint; the table must be recreated (cf. 036).

PRAGMA foreign_keys = OFF;

BEGIN;

ALTER TABLE tokens RENAME TO tokens_before_review_scope;

CREATE TABLE tokens (
    token_id     TEXT PRIMARY KEY,
    hash         TEXT NOT NULL UNIQUE,
    pepper_id    TEXT NOT NULL,
    project      TEXT NOT NULL REFERENCES projects(project_id),
    group_id     TEXT REFERENCES groups(group_id),
    doc_ref      TEXT,
    action_scope TEXT NOT NULL
                     CHECK (action_scope IN ('new', 'edit', 'workflow_decide', 'review')),
    issued_to    TEXT NOT NULL REFERENCES users(user_id),
    created_at   TEXT NOT NULL,
    expires_at   TEXT NOT NULL,
    consumed_at  TEXT,
    revoked_at   TEXT,
    scratch_dir  TEXT
);

INSERT INTO tokens (
    token_id, hash, pepper_id, project, group_id, doc_ref, action_scope,
    issued_to, created_at, expires_at, consumed_at, revoked_at, scratch_dir
)
SELECT
    token_id, hash, pepper_id, project, group_id, doc_ref, action_scope,
    issued_to, created_at, expires_at, consumed_at, revoked_at, scratch_dir
FROM tokens_before_review_scope;

DROP TABLE tokens_before_review_scope;

CREATE UNIQUE INDEX ux_tokens_hash ON tokens(hash);
CREATE INDEX idx_tokens_expires_at ON tokens(expires_at);
CREATE INDEX idx_tokens_issued_to ON tokens(issued_to);
CREATE INDEX idx_tokens_project ON tokens(project);

COMMIT;

PRAGMA foreign_keys = ON;
