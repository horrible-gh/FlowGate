-- 180: logical start commands. Retained for the Flow lifetime; no run FK or expiry.
CREATE TABLE IF NOT EXISTS graph_run_command_requests (
    group_id TEXT NOT NULL REFERENCES groups(group_id) ON DELETE CASCADE,
    command_request_id TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('accepted', 'void')),
    run_id TEXT,
    command_seq INTEGER,
    mode TEXT CHECK (mode IN ('step', 'full')),
    cause TEXT CHECK (cause IN ('user_start', 'user_command')),
    actor_user_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (group_id, command_request_id),
    CHECK ((state = 'void' AND run_id IS NULL AND command_seq IS NULL AND mode IS NULL AND cause IS NULL)
        OR (state = 'accepted' AND run_id IS NOT NULL AND command_seq IS NOT NULL AND command_seq > 0 AND cause IS NOT NULL))
);
