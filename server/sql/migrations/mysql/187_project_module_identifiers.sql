-- 187_project_module_identifiers.sql (mysql)
-- flowgate.v02gui.0012 (0001-B item B): a Group (module) identifier can be changed without
-- rewriting any stored reference. groups.module / documents.module / group_id / id_counter /
-- branch names keep the creation-time module key (the "permanent key"); this table only maps
-- exposed identifiers to that key.
--   is_current = 1  the identifier the module is shown and addressed by now (at most one per key;
--                   no row means the key itself is the exposed identifier)
--   is_current = 0  a retired identifier: still resolves to its key and can never be given to
--                   another module
-- The PRIMARY KEY makes an identifier belong to one module per project. New table only; rows of
-- existing tables are not touched. Safe to run again.
CREATE TABLE IF NOT EXISTS project_module_identifiers (
    project_id  VARCHAR(191) NOT NULL,
    identifier  VARCHAR(191) NOT NULL,
    module_key  VARCHAR(191) NOT NULL,
    is_current  INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    PRIMARY KEY (project_id, identifier)
);
CREATE INDEX IF NOT EXISTS idx_project_module_identifiers_key ON project_module_identifiers(project_id, module_key);
