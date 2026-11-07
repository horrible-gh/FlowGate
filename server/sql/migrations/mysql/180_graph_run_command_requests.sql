-- 180: logical start commands. Retained for the Flow lifetime; no run FK or expiry.
CREATE TABLE IF NOT EXISTS graph_run_command_requests (
    group_id VARCHAR(191) NOT NULL,
    command_request_id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    state VARCHAR(16) NOT NULL CHECK (state IN ('accepted', 'void')),
    run_id VARCHAR(64),
    command_seq INTEGER,
    mode VARCHAR(16) CHECK (mode IN ('step', 'full')),
    cause VARCHAR(32) CHECK (cause IN ('user_start', 'user_command')),
    actor_user_id VARCHAR(191) NOT NULL,
    created_at VARCHAR(40) NOT NULL,
    PRIMARY KEY (group_id, command_request_id),
    CONSTRAINT fk_graph_command_group FOREIGN KEY (group_id) REFERENCES groups(group_id) ON DELETE CASCADE,
    CHECK ((state = 'void' AND run_id IS NULL AND command_seq IS NULL AND mode IS NULL AND cause IS NULL)
        OR (state = 'accepted' AND run_id IS NOT NULL AND command_seq IS NOT NULL AND command_seq > 0 AND cause IS NOT NULL))
);
