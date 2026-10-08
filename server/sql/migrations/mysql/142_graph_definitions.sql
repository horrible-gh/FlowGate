-- 142_graph_definitions.sql — flowgate.v02.0045 DB (S3) (MySQL/MariaDB)
CREATE TABLE IF NOT EXISTS graph_drafts (
    group_id          VARCHAR(191) PRIMARY KEY,
    draft_version     INTEGER NOT NULL CHECK (draft_version >= 1),
    base_revision_no  INTEGER NULL CHECK (base_revision_no IS NULL OR base_revision_no >= 1),
    created_at        VARCHAR(40) NOT NULL,
    created_by        VARCHAR(191) NULL,
    updated_at        VARCHAR(40) NOT NULL,
    updated_by        VARCHAR(191) NULL,
    CONSTRAINT fk_graph_draft_group FOREIGN KEY (group_id) REFERENCES groups(group_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS graph_draft_nodes (
    group_id              VARCHAR(191) NOT NULL,
    node_key              VARCHAR(200) NOT NULL,
    ordinal               INTEGER NOT NULL CHECK (ordinal >= 0),
    node_type             VARCHAR(32) NOT NULL CHECK (node_type IN ('document', 'gate', 'custom')),
    doc_type              VARCHAR(16) NULL,
    palette_item_id       VARCHAR(128) NULL,
    label                 VARCHAR(200) NOT NULL,
    assignee_kind         VARCHAR(32) NULL CHECK (assignee_kind IS NULL OR assignee_kind IN ('human', 'ai')),
    assignee_user_id      VARCHAR(191) NULL,
    assignee_provider_id  VARCHAR(191) NULL,
    PRIMARY KEY (group_id, node_key),
    UNIQUE KEY uq_graph_draft_nodes_ordinal (group_id, ordinal),
    CHECK ((node_type = 'document' AND doc_type IS NOT NULL) OR (node_type <> 'document' AND doc_type IS NULL)),
    CHECK ((assignee_kind IS NULL AND assignee_user_id IS NULL AND assignee_provider_id IS NULL)
        OR (assignee_kind = 'human' AND assignee_user_id IS NOT NULL)
        OR (assignee_kind = 'ai' AND assignee_provider_id IS NOT NULL)),
    CONSTRAINT fk_graph_dnode_draft FOREIGN KEY (group_id) REFERENCES graph_drafts(group_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS graph_draft_edges (
    group_id         VARCHAR(191) NOT NULL,
    edge_id          VARCHAR(64) NOT NULL,
    ordinal          INTEGER NOT NULL CHECK (ordinal >= 0),
    source_node_key  VARCHAR(200) NOT NULL,
    target_node_key  VARCHAR(200) NOT NULL,
    condition_json   MEDIUMTEXT NULL,
    PRIMARY KEY (group_id, edge_id),
    UNIQUE KEY uq_graph_draft_edges_ordinal (group_id, ordinal),
    CONSTRAINT fk_graph_dedge_draft FOREIGN KEY (group_id) REFERENCES graph_drafts(group_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS graph_revisions (
    group_id              VARCHAR(191) NOT NULL,
    revision_no           INTEGER NOT NULL CHECK (revision_no >= 1),
    source_draft_version  INTEGER NOT NULL CHECK (source_draft_version >= 1),
    base_revision_no      INTEGER NULL,
    confirmed_at          VARCHAR(40) NOT NULL,
    confirmed_by          VARCHAR(191) NULL,
    PRIMARY KEY (group_id, revision_no),
    CONSTRAINT fk_graph_rev_group FOREIGN KEY (group_id) REFERENCES groups(group_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS graph_revision_nodes (
    group_id              VARCHAR(191) NOT NULL,
    revision_no           INTEGER NOT NULL,
    node_key              VARCHAR(200) NOT NULL,
    ordinal               INTEGER NOT NULL CHECK (ordinal >= 0),
    node_type             VARCHAR(32) NOT NULL CHECK (node_type IN ('document', 'gate', 'custom')),
    doc_type              VARCHAR(16) NULL,
    palette_item_id       VARCHAR(128) NULL,
    label                 VARCHAR(200) NOT NULL,
    assignee_kind         VARCHAR(32) NULL CHECK (assignee_kind IS NULL OR assignee_kind IN ('human', 'ai')),
    assignee_user_id      VARCHAR(191) NULL,
    assignee_provider_id  VARCHAR(191) NULL,
    PRIMARY KEY (group_id, revision_no, node_key),
    UNIQUE KEY uq_graph_rev_nodes_ordinal (group_id, revision_no, ordinal),
    CHECK ((node_type = 'document' AND doc_type IS NOT NULL) OR (node_type <> 'document' AND doc_type IS NULL)),
    CHECK ((assignee_kind IS NULL AND assignee_user_id IS NULL AND assignee_provider_id IS NULL)
        OR (assignee_kind = 'human' AND assignee_user_id IS NOT NULL)
        OR (assignee_kind = 'ai' AND assignee_provider_id IS NOT NULL)),
    CONSTRAINT fk_graph_rnode_rev FOREIGN KEY (group_id, revision_no)
        REFERENCES graph_revisions(group_id, revision_no) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS graph_revision_edges (
    group_id         VARCHAR(191) NOT NULL,
    revision_no      INTEGER NOT NULL,
    edge_id          VARCHAR(64) NOT NULL,
    ordinal          INTEGER NOT NULL CHECK (ordinal >= 0),
    source_node_key  VARCHAR(200) NOT NULL,
    target_node_key  VARCHAR(200) NOT NULL,
    condition_json   MEDIUMTEXT NULL,
    PRIMARY KEY (group_id, revision_no, edge_id),
    UNIQUE KEY uq_graph_rev_edges_ordinal (group_id, revision_no, ordinal),
    INDEX idx_graph_rev_edges_source (group_id, revision_no, source_node_key),
    INDEX idx_graph_rev_edges_target (group_id, revision_no, target_node_key),
    CONSTRAINT fk_graph_redge_rev FOREIGN KEY (group_id, revision_no)
        REFERENCES graph_revisions(group_id, revision_no) ON DELETE CASCADE,
    CONSTRAINT fk_graph_redge_source FOREIGN KEY (group_id, revision_no, source_node_key)
        REFERENCES graph_revision_nodes(group_id, revision_no, node_key) ON DELETE CASCADE,
    CONSTRAINT fk_graph_redge_target FOREIGN KEY (group_id, revision_no, target_node_key)
        REFERENCES graph_revision_nodes(group_id, revision_no, node_key) ON DELETE CASCADE
);
