-- 144_graph_runs.sql — flowgate.v02.0045 DB (S5) (PostgreSQL)
-- Graph Workflow run store: runs (one active per Flow), policy snapshot entries, node runs,
-- token <-> node run map, run event log (event_seq source), propagation records. Additive only.
-- status columns of graph_runs / graph_node_runs are open keys (P0008 §1.5, L0009 D-03): no CHECK.

CREATE TABLE IF NOT EXISTS graph_runs (
    run_id              TEXT PRIMARY KEY,
    group_id            TEXT NOT NULL REFERENCES groups(group_id) ON DELETE CASCADE,
    revision_no         INTEGER NOT NULL,
    status              TEXT NOT NULL,
    active_group_id     TEXT,
    started_at          TEXT NOT NULL,
    started_by          TEXT,
    stopped_at          TEXT,
    finished_at         TEXT,
    last_event_seq      INTEGER NOT NULL DEFAULT 0 CHECK (last_event_seq >= 0),
    policy_source       TEXT NOT NULL CHECK (policy_source IN ('flow', 'system_default')),
    policy_set_id       TEXT NOT NULL,
    policy_set_version  TEXT NOT NULL,
    policy_digest       VARCHAR(64) NOT NULL CHECK (policy_digest ~ '^[0-9a-f]{64}$'),
    updated_at          TEXT NOT NULL,
    FOREIGN KEY (group_id, revision_no) REFERENCES graph_revisions(group_id, revision_no) ON DELETE CASCADE,
    CHECK (active_group_id IS NULL OR active_group_id = group_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_graph_runs_active ON graph_runs(active_group_id);
CREATE INDEX IF NOT EXISTS idx_graph_runs_group_started ON graph_runs(group_id, started_at, run_id);

CREATE TABLE IF NOT EXISTS graph_run_policy_entries (
    run_id        TEXT NOT NULL REFERENCES graph_runs(run_id) ON DELETE CASCADE,
    policy_key    TEXT NOT NULL CHECK (policy_key IN ('start_node', 'edge_condition', 'parallelism', 'join',
                      'report_pair', 'rejection_path', 'completion', 'gate_node', 'custom_node')),
    policy_value  TEXT NOT NULL,
    impl_version  TEXT NOT NULL,
    PRIMARY KEY (run_id, policy_key)
);
CREATE INDEX IF NOT EXISTS idx_graph_run_policy_impl ON graph_run_policy_entries(policy_key, policy_value, impl_version);

CREATE TABLE IF NOT EXISTS graph_node_runs (
    run_id                 TEXT NOT NULL REFERENCES graph_runs(run_id) ON DELETE CASCADE,
    node_key               TEXT NOT NULL,
    status                 TEXT NOT NULL,
    result_doc_id          TEXT,
    root_bound             INTEGER NOT NULL DEFAULT 0 CHECK (root_bound IN (0, 1)),
    opened_at              TEXT,
    finished_at            TEXT,
    dispatch_token_issued  INTEGER CHECK (dispatch_token_issued IS NULL OR dispatch_token_issued IN (0, 1)),
    dispatch_issued_to     TEXT,
    dispatch_mention_id    TEXT,
    dispatch_error_code    TEXT,
    updated_at             TEXT NOT NULL,
    PRIMARY KEY (run_id, node_key),
    CHECK (dispatch_token_issued IS NOT NULL
        OR (dispatch_issued_to IS NULL AND dispatch_mention_id IS NULL AND dispatch_error_code IS NULL))
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_graph_node_runs_result_doc ON graph_node_runs(run_id, result_doc_id);

CREATE TABLE IF NOT EXISTS graph_run_token_map (
    token_id   TEXT PRIMARY KEY,
    run_id     TEXT NOT NULL,
    node_key   TEXT NOT NULL,
    mapped_at  TEXT NOT NULL,
    FOREIGN KEY (run_id, node_key) REFERENCES graph_node_runs(run_id, node_key) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_graph_token_map_node ON graph_run_token_map(run_id, node_key);

CREATE TABLE IF NOT EXISTS graph_run_events (
    run_id         TEXT NOT NULL REFERENCES graph_runs(run_id) ON DELETE CASCADE,
    event_seq      INTEGER NOT NULL CHECK (event_seq >= 1),
    event_type     TEXT NOT NULL CHECK (event_type IN ('graph_run_status_changed', 'graph_node_state_changed')),
    node_key       TEXT,
    from_status    TEXT,
    to_status      TEXT NOT NULL,
    cause          TEXT NOT NULL,
    result_doc_id  TEXT,
    canvas_state   TEXT,
    actor_user_id  TEXT,
    reason         TEXT,
    payload_json   TEXT NOT NULL,
    created_at     TEXT NOT NULL,
    PRIMARY KEY (run_id, event_seq),
    CHECK ((event_type = 'graph_node_state_changed' AND node_key IS NOT NULL)
        OR (event_type = 'graph_run_status_changed' AND node_key IS NULL))
);

CREATE TABLE IF NOT EXISTS graph_propagations (
    run_id            TEXT NOT NULL,
    source_event_seq  INTEGER NOT NULL,
    node_key          TEXT NOT NULL,
    outcome           TEXT NOT NULL CHECK (outcome IN ('approved', 'rejected')),
    status            TEXT NOT NULL CHECK (status IN ('pending', 'done')),
    created_at        TEXT NOT NULL,
    done_at           TEXT,
    PRIMARY KEY (run_id, source_event_seq),
    FOREIGN KEY (run_id, source_event_seq) REFERENCES graph_run_events(run_id, event_seq) ON DELETE CASCADE,
    FOREIGN KEY (run_id, node_key) REFERENCES graph_node_runs(run_id, node_key) ON DELETE CASCADE,
    CHECK ((status = 'pending' AND done_at IS NULL) OR (status = 'done' AND done_at IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS idx_graph_propagations_drain ON graph_propagations(run_id, status, source_event_seq);
