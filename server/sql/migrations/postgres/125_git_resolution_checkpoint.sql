-- Held finalize resolutions have a lifecycle independent of git_merge_session.
CREATE TABLE git_resolution_checkpoint (
    checkpoint_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    group_id TEXT NOT NULL,
    source_merge_id BIGINT NOT NULL,
    target_branch TEXT NOT NULL,
    source_branch TEXT NOT NULL,
    base_head VARCHAR(64) NOT NULL,
    merge_head VARCHAR(64) NOT NULL,
    expected_remote_head VARCHAR(64),
    resolved_paths TEXT NOT NULL,
    conflict_origins TEXT NOT NULL,
    provenance TEXT NOT NULL,
    state VARCHAR(16) NOT NULL CHECK (state IN ('active', 'consumed', 'invalidated')),
    replay_merge_id BIGINT,
    replay_result TEXT,
    created_at VARCHAR(40) NOT NULL,
    updated_at VARCHAR(40) NOT NULL
);
CREATE INDEX idx_git_resolution_checkpoint_owner
    ON git_resolution_checkpoint(project_id, group_id, state);
CREATE INDEX idx_git_resolution_checkpoint_replay
    ON git_resolution_checkpoint(replay_merge_id, state);
