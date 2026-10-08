-- 143_graph_engine_assignment.sql — flowgate.v02.0045 DB (S4)
-- Graph engine assignment record (at most one per Flow, never removed) and the root binding
-- (start node <-> pre-existing root R/B document), written in the same unit as revision 1. Additive only.
BEGIN;

CREATE TABLE IF NOT EXISTS graph_assignments (
    group_id              TEXT PRIMARY KEY REFERENCES groups(group_id) ON DELETE CASCADE,
    assigned_revision_no  INTEGER NOT NULL CHECK (assigned_revision_no >= 1),
    assigned_at           TEXT NOT NULL,
    assigned_by           TEXT,
    FOREIGN KEY (group_id, assigned_revision_no)
        REFERENCES graph_revisions(group_id, revision_no) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS graph_root_bindings (
    group_id              TEXT PRIMARY KEY REFERENCES graph_assignments(group_id) ON DELETE CASCADE,
    node_key              TEXT NOT NULL,
    doc_id                TEXT NOT NULL,
    doc_type              TEXT NOT NULL CHECK (doc_type IN ('R', 'B')),
    bound_at_revision_no  INTEGER NOT NULL CHECK (bound_at_revision_no >= 1),
    bound_at              TEXT NOT NULL,
    bound_by              TEXT,
    FOREIGN KEY (group_id, bound_at_revision_no, node_key)
        REFERENCES graph_revision_nodes(group_id, revision_no, node_key) ON DELETE CASCADE
);

-- SQLite only (§5.3): FK-independent cleanup for hard deletes run on a connection with foreign_keys OFF.
CREATE TRIGGER IF NOT EXISTS trg_graph_assignment_group_deleted AFTER DELETE ON groups BEGIN DELETE FROM graph_root_bindings WHERE group_id = OLD.group_id; DELETE FROM graph_assignments WHERE group_id = OLD.group_id; END;

COMMIT;
