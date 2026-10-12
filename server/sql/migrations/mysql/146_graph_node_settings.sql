-- 146_graph_node_settings.sql — flowgate.v02.0053 T#2 (card run settings) (MySQL/MariaDB)
CREATE TABLE IF NOT EXISTS graph_node_settings (
    group_id    VARCHAR(191) NOT NULL,
    node_key    VARCHAR(200) NOT NULL,
    field       VARCHAR(64) NOT NULL,
    value_json  TEXT NOT NULL,
    updated_at  VARCHAR(40) NOT NULL,
    updated_by  VARCHAR(191) NULL,
    PRIMARY KEY (group_id, node_key, field),
    CONSTRAINT fk_graph_node_settings_group FOREIGN KEY (group_id) REFERENCES groups(group_id) ON DELETE CASCADE
);
