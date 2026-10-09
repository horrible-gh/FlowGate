-- 142_graph_definitions.sql — flowgate.v02.0045 DB (S3) (PostgreSQL)
-- Graph Workflow definition store: one draft per Flow (nodes/edges replaced as a whole),
-- immutable confirmed revisions. Coordinates live in graph_node_positions, never here. Additive only.

CREATE TABLE IF NOT EXISTS graph_drafts (
    group_id          TEXT PRIMARY KEY REFERENCES groups(group_id) ON DELETE CASCADE,
    draft_version     INTEGER NOT NULL CHECK (draft_version >= 1),
    base_revision_no  INTEGER CHECK (base_revision_no IS NULL OR base_revision_no >= 1),
    created_at        TEXT NOT NULL,
    created_by        TEXT,
    updated_at        TEXT NOT NULL,
    updated_by        TEXT
);

CREATE TABLE IF NOT EXISTS graph_draft_nodes (
    group_id              TEXT NOT NULL REFERENCES graph_drafts(group_id) ON DELETE CASCADE,
    node_key              TEXT NOT NULL,
    ordinal               INTEGER NOT NULL CHECK (ordinal >= 0),
    node_type             TEXT NOT NULL CHECK (node_type IN ('document', 'gate', 'custom')),
    doc_type              TEXT,
    palette_item_id       TEXT,
    label                 TEXT NOT NULL,
    assignee_kind         TEXT CHECK (assignee_kind IS NULL OR assignee_kind IN ('human', 'ai')),
    assignee_user_id      TEXT,
    assignee_provider_id  TEXT,
    PRIMARY KEY (group_id, node_key),
    UNIQUE (group_id, ordinal),
    CHECK ((node_type = 'document' AND doc_type IS NOT NULL) OR (node_type <> 'document' AND doc_type IS NULL)),
    CHECK ((assignee_kind IS NULL AND assignee_user_id IS NULL AND assignee_provider_id IS NULL)
        OR (assignee_kind = 'human' AND assignee_user_id IS NOT NULL)
        OR (assignee_kind = 'ai' AND assignee_provider_id IS NOT NULL))
);

CREATE TABLE IF NOT EXISTS graph_draft_edges (
    group_id         TEXT NOT NULL REFERENCES graph_drafts(group_id) ON DELETE CASCADE,
    edge_id          TEXT NOT NULL,
    ordinal          INTEGER NOT NULL CHECK (ordinal >= 0),
    source_node_key  TEXT NOT NULL,
    target_node_key  TEXT NOT NULL,
    condition_json   TEXT,
    PRIMARY KEY (group_id, edge_id),
    UNIQUE (group_id, ordinal)
);

CREATE TABLE IF NOT EXISTS graph_revisions (
    group_id              TEXT NOT NULL REFERENCES groups(group_id) ON DELETE CASCADE,
    revision_no           INTEGER NOT NULL CHECK (revision_no >= 1),
    source_draft_version  INTEGER NOT NULL CHECK (source_draft_version >= 1),
    base_revision_no      INTEGER,
    confirmed_at          TEXT NOT NULL,
    confirmed_by          TEXT,
    PRIMARY KEY (group_id, revision_no)
);

CREATE TABLE IF NOT EXISTS graph_revision_nodes (
    group_id              TEXT NOT NULL,
    revision_no           INTEGER NOT NULL,
    node_key              TEXT NOT NULL,
    ordinal               INTEGER NOT NULL CHECK (ordinal >= 0),
    node_type             TEXT NOT NULL CHECK (node_type IN ('document', 'gate', 'custom')),
    doc_type              TEXT,
    palette_item_id       TEXT,
    label                 TEXT NOT NULL,
    assignee_kind         TEXT CHECK (assignee_kind IS NULL OR assignee_kind IN ('human', 'ai')),
    assignee_user_id      TEXT,
    assignee_provider_id  TEXT,
    PRIMARY KEY (group_id, revision_no, node_key),
    UNIQUE (group_id, revision_no, ordinal),
    FOREIGN KEY (group_id, revision_no) REFERENCES graph_revisions(group_id, revision_no) ON DELETE CASCADE,
    CHECK ((node_type = 'document' AND doc_type IS NOT NULL) OR (node_type <> 'document' AND doc_type IS NULL)),
    CHECK ((assignee_kind IS NULL AND assignee_user_id IS NULL AND assignee_provider_id IS NULL)
        OR (assignee_kind = 'human' AND assignee_user_id IS NOT NULL)
        OR (assignee_kind = 'ai' AND assignee_provider_id IS NOT NULL))
);

CREATE TABLE IF NOT EXISTS graph_revision_edges (
    group_id         TEXT NOT NULL,
    revision_no      INTEGER NOT NULL,
    edge_id          TEXT NOT NULL,
    ordinal          INTEGER NOT NULL CHECK (ordinal >= 0),
    source_node_key  TEXT NOT NULL,
    target_node_key  TEXT NOT NULL,
    condition_json   TEXT,
    PRIMARY KEY (group_id, revision_no, edge_id),
    UNIQUE (group_id, revision_no, ordinal),
    FOREIGN KEY (group_id, revision_no) REFERENCES graph_revisions(group_id, revision_no) ON DELETE CASCADE,
    FOREIGN KEY (group_id, revision_no, source_node_key)
        REFERENCES graph_revision_nodes(group_id, revision_no, node_key) ON DELETE CASCADE,
    FOREIGN KEY (group_id, revision_no, target_node_key)
        REFERENCES graph_revision_nodes(group_id, revision_no, node_key) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_graph_rev_edges_source ON graph_revision_edges(group_id, revision_no, source_node_key);
CREATE INDEX IF NOT EXISTS idx_graph_rev_edges_target ON graph_revision_edges(group_id, revision_no, target_node_key);
