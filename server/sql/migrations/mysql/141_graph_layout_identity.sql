-- 141_graph_layout_identity.sql — flowgate.v02.0045 DB (S2) (MySQL/MariaDB)
CREATE TABLE IF NOT EXISTS graph_flow_guards (
    group_id    VARCHAR(191) PRIMARY KEY,
    guard_seq   INTEGER NOT NULL DEFAULT 0 CHECK (guard_seq >= 0),
    created_at  VARCHAR(40) NOT NULL,
    CONSTRAINT fk_graph_guard_group FOREIGN KEY (group_id) REFERENCES groups(group_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS graph_node_positions (
    group_id    VARCHAR(191) NOT NULL,
    node_key    VARCHAR(200) NOT NULL,
    x           DOUBLE NOT NULL,
    y           DOUBLE NOT NULL,
    updated_at  VARCHAR(40) NOT NULL,
    updated_by  VARCHAR(191) NULL,
    PRIMARY KEY (group_id, node_key),
    CONSTRAINT fk_graph_pos_group FOREIGN KEY (group_id) REFERENCES groups(group_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS graph_viewports (
    group_id    VARCHAR(191) NOT NULL,
    user_id     VARCHAR(191) NOT NULL,
    x           DOUBLE NOT NULL,
    y           DOUBLE NOT NULL,
    zoom        DOUBLE NOT NULL CHECK (zoom > 0),
    updated_at  VARCHAR(40) NOT NULL,
    PRIMARY KEY (group_id, user_id),
    INDEX idx_graph_viewports_user (user_id),
    CONSTRAINT fk_graph_vp_group FOREIGN KEY (group_id) REFERENCES groups(group_id) ON DELETE CASCADE,
    CONSTRAINT fk_graph_vp_user FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS graph_identity_records (
    group_id      VARCHAR(191) PRIMARY KEY,
    sequence_tag  VARCHAR(128) NULL,
    recorded_at   VARCHAR(40) NOT NULL,
    recorded_by   VARCHAR(191) NULL,
    CONSTRAINT fk_graph_idrec_group FOREIGN KEY (group_id) REFERENCES groups(group_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS graph_identity_rows (
    group_id          VARCHAR(191) NOT NULL,
    node_key          VARCHAR(200) NOT NULL,
    identity_source   VARCHAR(32) NOT NULL CHECK (identity_source IN ('document', 'sequence_row', 'sequence_slot')),
    item_id           INTEGER NOT NULL,
    item_seq          INTEGER NOT NULL,
    item_type         VARCHAR(16) NOT NULL,
    label             MEDIUMTEXT NOT NULL,
    sort_order        INTEGER NOT NULL,
    source_wp_card_id VARCHAR(128) NULL,
    result_doc_id     VARCHAR(191) NULL,
    is_protected      INTEGER NOT NULL DEFAULT 0 CHECK (is_protected IN (0, 1)),
    PRIMARY KEY (group_id, node_key),
    CHECK (identity_source <> 'document' OR result_doc_id IS NOT NULL),
    CONSTRAINT fk_graph_idrow_rec FOREIGN KEY (group_id) REFERENCES graph_identity_records(group_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS graph_retired_node_keys (
    group_id    VARCHAR(191) NOT NULL,
    node_key    VARCHAR(200) NOT NULL,
    reason      VARCHAR(32) NOT NULL CHECK (reason IN ('rematch', 'supersede', 'root_binding', 'draft_removed', 'revision_removed')),
    retired_at  VARCHAR(40) NOT NULL,
    PRIMARY KEY (group_id, node_key),
    CONSTRAINT fk_graph_retired_group FOREIGN KEY (group_id) REFERENCES groups(group_id) ON DELETE CASCADE
);
