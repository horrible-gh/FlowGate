-- 211_graph_card_numbers.sql — card numbers of a Flow (MySQL/MariaDB)
-- The display id of a card is F<flow number>-<card number> (F77-12). The Flow number comes from the group id and is
-- never stored; the card number is stored here per card (Flow, node_key) from a per-Flow counter that only goes up,
-- so a number never changes and the number of a deleted card is never handed out again. node_key has no FK (nodes
-- change with the draft / revisions; a deleted node's row stays, like its title state and memos).
-- No stored id (group id, document id, work plan card id, node key) is changed by this migration.
-- Additive; rollback: DROP TABLE graph_card_numbers; DROP TABLE graph_card_counters.
CREATE TABLE IF NOT EXISTS graph_card_counters (
    group_id      VARCHAR(191) NOT NULL,
    next_card_no  INTEGER NOT NULL DEFAULT 1,
    created_at    VARCHAR(40) NOT NULL,
    updated_at    VARCHAR(40) NOT NULL,
    PRIMARY KEY (group_id),
    CONSTRAINT fk_graph_card_counters_group FOREIGN KEY (group_id) REFERENCES groups(group_id) ON DELETE CASCADE,
    CONSTRAINT ck_graph_card_counters_next CHECK (next_card_no >= 1)
);

CREATE TABLE IF NOT EXISTS graph_card_numbers (
    group_id    VARCHAR(191) NOT NULL,
    node_key    VARCHAR(200) NOT NULL,
    card_no     INTEGER NOT NULL,
    created_at  VARCHAR(40) NOT NULL,
    PRIMARY KEY (group_id, node_key),
    CONSTRAINT uq_graph_card_numbers_no UNIQUE (group_id, card_no),
    CONSTRAINT fk_graph_card_numbers_group FOREIGN KEY (group_id) REFERENCES groups(group_id) ON DELETE CASCADE,
    CONSTRAINT ck_graph_card_numbers_no CHECK (card_no >= 1)
);
