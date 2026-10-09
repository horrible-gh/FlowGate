-- 143_graph_engine_assignment.sql — flowgate.v02.0045 DB (S4) (MySQL/MariaDB)
CREATE TABLE IF NOT EXISTS graph_assignments (
    group_id              VARCHAR(191) PRIMARY KEY,
    assigned_revision_no  INTEGER NOT NULL,
    assigned_at           VARCHAR(40) NOT NULL,
    assigned_by           VARCHAR(191) NULL,
    CONSTRAINT fk_graph_assign_group FOREIGN KEY (group_id) REFERENCES groups(group_id) ON DELETE CASCADE,
    CONSTRAINT fk_graph_assign_rev FOREIGN KEY (group_id, assigned_revision_no)
        REFERENCES graph_revisions(group_id, revision_no) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS graph_root_bindings (
    group_id              VARCHAR(191) PRIMARY KEY,
    node_key              VARCHAR(200) NOT NULL,
    doc_id                VARCHAR(191) NOT NULL,
    doc_type              VARCHAR(16) NOT NULL CHECK (doc_type IN ('R', 'B')),
    bound_at_revision_no  INTEGER NOT NULL,
    bound_at              VARCHAR(40) NOT NULL,
    bound_by              VARCHAR(191) NULL,
    INDEX idx_graph_root_binding_node (group_id, bound_at_revision_no, node_key),
    CONSTRAINT fk_graph_rootb_assign FOREIGN KEY (group_id) REFERENCES graph_assignments(group_id) ON DELETE CASCADE,
    CONSTRAINT fk_graph_rootb_node FOREIGN KEY (group_id, bound_at_revision_no, node_key)
        REFERENCES graph_revision_nodes(group_id, revision_no, node_key) ON DELETE CASCADE
);
