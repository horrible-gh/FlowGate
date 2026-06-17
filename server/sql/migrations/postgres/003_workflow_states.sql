-- 003_workflow_states.sql
-- D017 r1: expand the groups.status CHECK constraint (add draft/in_progress/clarifying/approved/closed)
-- In SQLite, changing a CHECK constraint requires recreating the table.
-- Existing record status values are preserved (OPEN/CLOSED/CANCELLED -> not used in the new app).

-- 1. Preserve data in a temporary table
CREATE TABLE IF NOT EXISTS _groups_bak AS SELECT * FROM groups;
-- 2. Drop the existing table (foreign keys must be disabled)
-- [pg-fk-rebuild] preserve inbound FOREIGN KEYs across the drop+recreate of "groups"
DO $$
DECLARE _stmt text;
BEGIN
    CREATE TEMP TABLE _fk_rb_groups ON COMMIT DROP AS
            SELECT 'ALTER TABLE ' || quote_ident(n.nspname) || '.' || quote_ident(c.relname)
                   || ' ADD CONSTRAINT ' || quote_ident(con.conname) || ' ' || pg_get_constraintdef(con.oid) AS stmt
            FROM pg_constraint con
            JOIN pg_class c ON c.oid = con.conrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE con.contype = 'f' AND con.confrelid = to_regclass('groups')
              AND con.conrelid <> con.confrelid;
    FOR _stmt IN
        SELECT 'ALTER TABLE ' || quote_ident(n.nspname) || '.' || quote_ident(c.relname)
               || ' DROP CONSTRAINT ' || quote_ident(con.conname)
        FROM pg_constraint con
        JOIN pg_class c ON c.oid = con.conrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE con.contype = 'f' AND con.confrelid = to_regclass('groups')
          AND con.conrelid <> con.confrelid
    LOOP
        EXECUTE _stmt;
    END LOOP;
END $$;
DROP TABLE groups;
-- 3. Recreate with the expanded CHECK constraint
CREATE TABLE groups (
    group_id    TEXT PRIMARY KEY,
    project_id  TEXT NOT NULL REFERENCES projects(project_id),
    module      TEXT NOT NULL DEFAULT '__ALL__',
    parent_id   TEXT REFERENCES groups(group_id),
    title       TEXT NOT NULL,
    priority    TEXT,
    status      TEXT NOT NULL DEFAULT 'draft'
                    CHECK (status IN (
                        'draft', 'in_progress', 'clarifying',
                        'approved', 'closed',
                        'OPEN', 'CLOSED', 'CANCELLED'
                    )),
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    closed_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_groups_project_module ON groups(project_id, module);
CREATE INDEX IF NOT EXISTS idx_groups_parent         ON groups(parent_id);
CREATE INDEX IF NOT EXISTS idx_groups_status         ON groups(status);
-- 4. Restore data
INSERT INTO groups SELECT * FROM _groups_bak;
DROP TABLE _groups_bak;

-- [pg-fk-rebuild] restore inbound FOREIGN KEYs for "groups"
DO $$
DECLARE _stmt text;
BEGIN
    IF to_regclass('pg_temp._fk_rb_groups') IS NOT NULL THEN
        FOR _stmt IN SELECT stmt FROM _fk_rb_groups LOOP
            EXECUTE _stmt;
        END LOOP;
    END IF;
END $$;
