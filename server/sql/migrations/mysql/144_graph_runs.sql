-- 144_graph_runs.sql — flowgate.v02.0045 DB (S5) (MySQL/MariaDB)
CREATE TABLE IF NOT EXISTS graph_runs (
    run_id              VARCHAR(64) PRIMARY KEY,
    group_id            VARCHAR(191) NOT NULL,
    revision_no         INTEGER NOT NULL,
    status              VARCHAR(32) NOT NULL,
    active_group_id     VARCHAR(191) NULL,
    started_at          VARCHAR(40) NOT NULL,
    started_by          VARCHAR(191) NULL,
    stopped_at          VARCHAR(40) NULL,
    finished_at         VARCHAR(40) NULL,
    last_event_seq      INTEGER NOT NULL DEFAULT 0 CHECK (last_event_seq >= 0),
    policy_source       VARCHAR(32) NOT NULL CHECK (policy_source IN ('flow', 'system_default')),
    policy_set_id       VARCHAR(64) NOT NULL,
    policy_set_version  VARCHAR(64) NOT NULL,
    policy_digest       CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL
                            CHECK (CHAR_LENGTH(policy_digest) = 64 AND policy_digest REGEXP '^[0-9a-f]{64}$'),
    updated_at          VARCHAR(40) NOT NULL,
    UNIQUE KEY idx_graph_runs_active (active_group_id),
    INDEX idx_graph_runs_group_started (group_id, started_at, run_id),
    CONSTRAINT fk_graph_run_group FOREIGN KEY (group_id) REFERENCES groups(group_id) ON DELETE CASCADE,
    CONSTRAINT fk_graph_run_rev FOREIGN KEY (group_id, revision_no)
        REFERENCES graph_revisions(group_id, revision_no) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS graph_run_policy_entries (
    run_id        VARCHAR(64) NOT NULL,
    policy_key    VARCHAR(32) NOT NULL CHECK (policy_key IN ('start_node', 'edge_condition', 'parallelism', 'join',
                      'report_pair', 'rejection_path', 'completion', 'gate_node', 'custom_node')),
    policy_value  VARCHAR(64) NOT NULL,
    impl_version  VARCHAR(64) NOT NULL,
    PRIMARY KEY (run_id, policy_key),
    INDEX idx_graph_run_policy_impl (policy_key, policy_value, impl_version),
    CONSTRAINT fk_graph_rpe_run FOREIGN KEY (run_id) REFERENCES graph_runs(run_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS graph_node_runs (
    run_id                 VARCHAR(64) NOT NULL,
    node_key               VARCHAR(200) NOT NULL,
    status                 VARCHAR(32) NOT NULL,
    result_doc_id          VARCHAR(191) NULL,
    root_bound             INTEGER NOT NULL DEFAULT 0 CHECK (root_bound IN (0, 1)),
    opened_at              VARCHAR(40) NULL,
    finished_at            VARCHAR(40) NULL,
    dispatch_token_issued  INTEGER NULL CHECK (dispatch_token_issued IS NULL OR dispatch_token_issued IN (0, 1)),
    dispatch_issued_to     VARCHAR(191) NULL,
    dispatch_mention_id    VARCHAR(191) NULL,
    dispatch_error_code    VARCHAR(64) NULL,
    updated_at             VARCHAR(40) NOT NULL,
    PRIMARY KEY (run_id, node_key),
    UNIQUE KEY idx_graph_node_runs_result_doc (run_id, result_doc_id),
    CHECK (dispatch_token_issued IS NOT NULL
        OR (dispatch_issued_to IS NULL AND dispatch_mention_id IS NULL AND dispatch_error_code IS NULL)),
    CONSTRAINT fk_graph_nrun_run FOREIGN KEY (run_id) REFERENCES graph_runs(run_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS graph_run_token_map (
    token_id   VARCHAR(191) PRIMARY KEY,
    run_id     VARCHAR(64) NOT NULL,
    node_key   VARCHAR(200) NOT NULL,
    mapped_at  VARCHAR(40) NOT NULL,
    INDEX idx_graph_token_map_node (run_id, node_key),
    CONSTRAINT fk_graph_tmap_nrun FOREIGN KEY (run_id, node_key)
        REFERENCES graph_node_runs(run_id, node_key) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS graph_run_events (
    run_id         VARCHAR(64) NOT NULL,
    event_seq      INTEGER NOT NULL CHECK (event_seq >= 1),
    event_type     VARCHAR(32) NOT NULL CHECK (event_type IN ('graph_run_status_changed', 'graph_node_state_changed')),
    node_key       VARCHAR(200) NULL,
    from_status    VARCHAR(32) NULL,
    to_status      VARCHAR(32) NOT NULL,
    cause          VARCHAR(64) NOT NULL,
    result_doc_id  VARCHAR(191) NULL,
    canvas_state   VARCHAR(32) NULL,
    actor_user_id  VARCHAR(191) NULL,
    reason         MEDIUMTEXT NULL,
    payload_json   MEDIUMTEXT NOT NULL,
    created_at     VARCHAR(40) NOT NULL,
    PRIMARY KEY (run_id, event_seq),
    CHECK ((event_type = 'graph_node_state_changed' AND node_key IS NOT NULL)
        OR (event_type = 'graph_run_status_changed' AND node_key IS NULL)),
    CONSTRAINT fk_graph_revt_run FOREIGN KEY (run_id) REFERENCES graph_runs(run_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS graph_propagations (
    run_id            VARCHAR(64) NOT NULL,
    source_event_seq  INTEGER NOT NULL,
    node_key          VARCHAR(200) NOT NULL,
    outcome           VARCHAR(32) NOT NULL CHECK (outcome IN ('approved', 'rejected')),
    status            VARCHAR(32) NOT NULL CHECK (status IN ('pending', 'done')),
    created_at        VARCHAR(40) NOT NULL,
    done_at           VARCHAR(40) NULL,
    PRIMARY KEY (run_id, source_event_seq),
    INDEX idx_graph_propagations_drain (run_id, status, source_event_seq),
    INDEX idx_graph_propagations_node (run_id, node_key),
    CHECK ((status = 'pending' AND done_at IS NULL) OR (status = 'done' AND done_at IS NOT NULL)),
    CONSTRAINT fk_graph_prop_event FOREIGN KEY (run_id, source_event_seq)
        REFERENCES graph_run_events(run_id, event_seq) ON DELETE CASCADE,
    CONSTRAINT fk_graph_prop_nrun FOREIGN KEY (run_id, node_key)
        REFERENCES graph_node_runs(run_id, node_key) ON DELETE CASCADE
);
