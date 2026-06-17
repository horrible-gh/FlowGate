-- T392: change document_types.series / id_counter.series from 'requirements' -> 'general'
-- Recreating the tables is required to update the SQLite CHECK constraints.
-- Idempotency: rerunning has no effect on rows already set to 'general'.

-- ── Recreate the document_types table ────────────────────────────────────────
CREATE TABLE document_types_new (
    id           SERIAL PRIMARY KEY,
    project_id   TEXT REFERENCES projects(project_id) ON DELETE CASCADE,
    type_code    TEXT NOT NULL,
    type_name    TEXT NOT NULL,
    series       TEXT NOT NULL
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
-- [pg-fk-rebuild] preserve inbound FOREIGN KEYs across the drop+recreate of "document_types"
DO $$
DECLARE _stmt text;
BEGIN
    CREATE TEMP TABLE _fk_rb_document_types ON COMMIT DROP AS
            SELECT 'ALTER TABLE ' || quote_ident(n.nspname) || '.' || quote_ident(c.relname)
                   || ' ADD CONSTRAINT ' || quote_ident(con.conname) || ' ' || pg_get_constraintdef(con.oid) AS stmt
            FROM pg_constraint con
            JOIN pg_class c ON c.oid = con.conrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE con.contype = 'f' AND con.confrelid = to_regclass('document_types')
              AND con.conrelid <> con.confrelid;
    FOR _stmt IN
        SELECT 'ALTER TABLE ' || quote_ident(n.nspname) || '.' || quote_ident(c.relname)
               || ' DROP CONSTRAINT ' || quote_ident(con.conname)
        FROM pg_constraint con
        JOIN pg_class c ON c.oid = con.conrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE con.contype = 'f' AND con.confrelid = to_regclass('document_types')
          AND con.conrelid <> con.confrelid
    LOOP
        EXECUTE _stmt;
    END LOOP;
END $$;
DROP TABLE document_types;
ALTER TABLE document_types_new RENAME TO document_types;
CREATE INDEX IF NOT EXISTS idx_doc_types_project  ON document_types(project_id);
CREATE INDEX IF NOT EXISTS idx_doc_types_series   ON document_types(series);
CREATE INDEX IF NOT EXISTS idx_doc_types_active   ON document_types(is_active);
CREATE UNIQUE INDEX IF NOT EXISTS ux_doc_types_global
    ON document_types(series, type_code)
    WHERE project_id IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS ux_doc_types_project
    ON document_types(project_id, series, type_code)
    WHERE project_id IS NOT NULL;
-- ── Recreate the id_counter table ────────────────────────────────────────────
CREATE TABLE id_counter_new (
    project_id    TEXT NOT NULL,
    module        TEXT NOT NULL DEFAULT '__ALL__',
    group_seq     TEXT NOT NULL DEFAULT '',
    sub_group_seq TEXT NOT NULL DEFAULT '',
    series        TEXT NOT NULL DEFAULT ''
                      CHECK (series IN ('', 'general', 'instruction', 'design', 'work', 'action')),
    type_code     TEXT NOT NULL DEFAULT '',
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
-- [pg-fk-rebuild] preserve inbound FOREIGN KEYs across the drop+recreate of "id_counter"
DO $$
DECLARE _stmt text;
BEGIN
    CREATE TEMP TABLE _fk_rb_id_counter ON COMMIT DROP AS
            SELECT 'ALTER TABLE ' || quote_ident(n.nspname) || '.' || quote_ident(c.relname)
                   || ' ADD CONSTRAINT ' || quote_ident(con.conname) || ' ' || pg_get_constraintdef(con.oid) AS stmt
            FROM pg_constraint con
            JOIN pg_class c ON c.oid = con.conrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE con.contype = 'f' AND con.confrelid = to_regclass('id_counter')
              AND con.conrelid <> con.confrelid;
    FOR _stmt IN
        SELECT 'ALTER TABLE ' || quote_ident(n.nspname) || '.' || quote_ident(c.relname)
               || ' DROP CONSTRAINT ' || quote_ident(con.conname)
        FROM pg_constraint con
        JOIN pg_class c ON c.oid = con.conrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE con.contype = 'f' AND con.confrelid = to_regclass('id_counter')
          AND con.conrelid <> con.confrelid
    LOOP
        EXECUTE _stmt;
    END LOOP;
END $$;
DROP TABLE id_counter;
ALTER TABLE id_counter_new RENAME TO id_counter;

-- [pg-fk-rebuild] restore inbound FOREIGN KEYs for "document_types"
DO $$
DECLARE _stmt text;
BEGIN
    IF to_regclass('pg_temp._fk_rb_document_types') IS NOT NULL THEN
        FOR _stmt IN SELECT stmt FROM _fk_rb_document_types LOOP
            EXECUTE _stmt;
        END LOOP;
    END IF;
END $$;

-- [pg-fk-rebuild] restore inbound FOREIGN KEYs for "id_counter"
DO $$
DECLARE _stmt text;
BEGIN
    IF to_regclass('pg_temp._fk_rb_id_counter') IS NOT NULL THEN
        FOR _stmt IN SELECT stmt FROM _fk_rb_id_counter LOOP
            EXECUTE _stmt;
        END LOOP;
    END IF;
END $$;
