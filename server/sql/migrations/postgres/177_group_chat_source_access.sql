-- T-2 / 0141: preserve legacy user-wide choices; groups start read_only (no rows).
-- No existing capability or pending legacy claim is copied to any group.
ALTER TABLE user_chat_source_access RENAME TO user_chat_source_access_legacy_177;

CREATE TABLE user_chat_source_access (
    user_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    group_id TEXT NOT NULL,
    source_access_mode TEXT NOT NULL,
    one_shot_token_id TEXT,
    one_shot_claimed_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CONSTRAINT pk_ucsa_group PRIMARY KEY (user_id, project_id, group_id),
    CONSTRAINT ck_ucsa_group_mode CHECK (source_access_mode IN ('read_only', 'edit', 'edit_once')),
    CONSTRAINT ck_ucsa_group_marker CHECK ((one_shot_token_id IS NULL) = (one_shot_claimed_at IS NULL)),
    CONSTRAINT fk_ucsa_group_user FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE,
    CONSTRAINT fk_ucsa_group_token FOREIGN KEY (one_shot_token_id) REFERENCES tokens(token_id) ON DELETE RESTRICT,
    CONSTRAINT fk_ucsa_group_group FOREIGN KEY (group_id) REFERENCES groups(group_id) ON DELETE CASCADE
);

CREATE UNIQUE INDEX idx_ucsa_group_one_shot_token_id
    ON user_chat_source_access(one_shot_token_id);
