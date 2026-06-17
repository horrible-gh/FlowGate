SET FOREIGN_KEY_CHECKS=0;
-- T392: change document_types.series / id_counter.series from 'requirements' -> 'general'
-- Recreating the tables is required to update the SQLite CHECK constraints.
-- Idempotency: rerunning has no effect on rows already set to 'general'.

-- ── Recreate the document_types table ────────────────────────────────────────
CREATE TABLE document_types_new (
    id           INTEGER PRIMARY KEY AUTO_INCREMENT,
    project_id   VARCHAR(191) REFERENCES projects(project_id) ON DELETE CASCADE,
    type_code    VARCHAR(191) NOT NULL,
    type_name    TEXT NOT NULL,
    series       VARCHAR(191) NOT NULL
                     CHECK (series IN ('general', 'instruction', 'design', 'work', 'action')),
    is_system    INTEGER NOT NULL DEFAULT 0,
    is_active    INTEGER NOT NULL DEFAULT 1,
    sort_order   INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    color        TEXT,
    description  TEXT,
    locale       VARCHAR(8) NOT NULL DEFAULT 'ko'
);
INSERT INTO document_types_new
    (id, project_id, type_code, type_name, series,
     is_system, is_active, sort_order, created_at, updated_at,
     color, description, locale)
SELECT
    id, project_id, type_code, type_name,
    CASE WHEN series = 'requirements' THEN 'general' ELSE series END,
    is_system, is_active, sort_order, created_at, updated_at,
    color,
    CASE WHEN description IS NULL THEN NULL ELSE description END,
    COALESCE(locale, 'ko')
FROM document_types;
DROP TABLE document_types;
ALTER TABLE document_types_new RENAME TO document_types;
CREATE INDEX IF NOT EXISTS idx_doc_types_project  ON document_types(project_id);
CREATE INDEX IF NOT EXISTS idx_doc_types_series   ON document_types(series);
CREATE INDEX IF NOT EXISTS idx_doc_types_active   ON document_types(is_active);
ALTER TABLE document_types ADD COLUMN IF NOT EXISTS _g_ux_doc_types_global VARCHAR(255) GENERATED ALWAYS AS (CASE WHEN project_id IS NULL THEN CONCAT_WS(CHAR(31), series, type_code) ELSE NULL END) STORED;
CREATE UNIQUE INDEX IF NOT EXISTS ux_doc_types_global ON document_types (_g_ux_doc_types_global);
CREATE UNIQUE INDEX IF NOT EXISTS ux_doc_types_project ON document_types (project_id, series, type_code);
-- ── Recreate the id_counter table ────────────────────────────────────────────
CREATE TABLE id_counter_new (
    project_id    VARCHAR(64) NOT NULL,
    module        VARCHAR(64) NOT NULL DEFAULT '__ALL__',
    group_seq     VARCHAR(64) NOT NULL DEFAULT '',
    sub_group_seq VARCHAR(64) NOT NULL DEFAULT '',
    series        VARCHAR(64) NOT NULL DEFAULT ''
                      CHECK (series IN ('', 'general', 'instruction', 'design', 'work', 'action')),
    type_code     VARCHAR(64) NOT NULL DEFAULT '',
    last_seq      INTEGER NOT NULL DEFAULT 0,
    updated_at    TEXT NOT NULL,
    PRIMARY KEY (project_id, module, group_seq, sub_group_seq, series, type_code)
);
INSERT INTO id_counter_new
    (project_id, module, group_seq, sub_group_seq, series, type_code, last_seq, updated_at)
SELECT
    project_id, module, group_seq, sub_group_seq,
    CASE WHEN series = 'requirements' THEN 'general' ELSE series END,
    type_code, last_seq, updated_at
FROM id_counter;
DROP TABLE id_counter;
ALTER TABLE id_counter_new RENAME TO id_counter;
SET FOREIGN_KEY_CHECKS=1;
