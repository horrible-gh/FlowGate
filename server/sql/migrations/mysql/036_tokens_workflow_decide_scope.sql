SET FOREIGN_KEY_CHECKS=0;
-- Allow worker tokens dedicated to deciding an R document workflow.

ALTER TABLE tokens RENAME TO tokens_before_workflow_decide_scope;
CREATE TABLE tokens (
    token_id     VARCHAR(191) PRIMARY KEY,
    hash         VARCHAR(191) NOT NULL UNIQUE,
    pepper_id    TEXT NOT NULL,
    project      VARCHAR(191) NOT NULL REFERENCES projects(project_id),
    group_id     VARCHAR(191) REFERENCES groups(group_id),
    doc_ref      TEXT,
    action_scope TEXT NOT NULL
                     CHECK (action_scope IN ('new', 'edit', 'workflow_decide')),
    issued_to    VARCHAR(191) NOT NULL REFERENCES users(user_id),
    created_at   TEXT NOT NULL,
    expires_at   VARCHAR(191) NOT NULL,
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
FROM tokens_before_workflow_decide_scope;
DROP TABLE tokens_before_workflow_decide_scope;
CREATE UNIQUE INDEX ux_tokens_hash ON tokens(hash);
CREATE INDEX idx_tokens_expires_at ON tokens(expires_at);
CREATE INDEX idx_tokens_issued_to ON tokens(issued_to);
CREATE INDEX idx_tokens_project ON tokens(project);
SET FOREIGN_KEY_CHECKS=1;
