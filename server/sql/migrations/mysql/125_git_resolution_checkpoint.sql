-- Held finalize resolutions have a lifecycle independent of git_merge_session.
CREATE TABLE git_resolution_checkpoint (
    checkpoint_id VARCHAR(64) PRIMARY KEY,
    project_id VARCHAR(191) NOT NULL,
    group_id VARCHAR(191) NOT NULL,
    source_merge_id BIGINT NOT NULL,
    target_branch VARCHAR(191) NOT NULL,
    source_branch VARCHAR(191) NOT NULL,
    base_head VARCHAR(64) NOT NULL,
    merge_head VARCHAR(64) NOT NULL,
    expected_remote_head VARCHAR(64),
    resolved_paths LONGTEXT NOT NULL,
    conflict_origins LONGTEXT NOT NULL,
    provenance LONGTEXT NOT NULL,
    state VARCHAR(16) NOT NULL CHECK (state IN ('active', 'consumed', 'invalidated')),
    replay_merge_id BIGINT,
    replay_result LONGTEXT,
    created_at VARCHAR(40) NOT NULL,
    updated_at VARCHAR(40) NOT NULL
);
CREATE INDEX idx_git_resolution_checkpoint_owner
    ON git_resolution_checkpoint(project_id, group_id, state);
CREATE INDEX idx_git_resolution_checkpoint_replay
    ON git_resolution_checkpoint(replay_merge_id, state);
