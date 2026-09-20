-- 115_ai_invoke_run_write_plan_bool_repair.sql
-- flowgate.default.0546 T0004 (NR0003): migration 105 declared
-- ai_invoke_runs.write_requested_by_human / allow_test_edits as PostgreSQL BOOLEAN,
-- while every other boolean-shaped column already on this table (resumable,
-- turn_limit_exhausted, oracle_mismatch, source_dirty -- see 076b) is INTEGER 0/1: the
-- writer (db/ai_invoke_runs.py upsert()) binds None/0/1 for every one of them, one
-- statement across sqlite/postgres/mysql, and PostgreSQL will not coerce an integer
-- into a boolean column. That drift is what raised
--
--   psycopg2.errors.DatatypeMismatch: column "write_requested_by_human" is of type
--   boolean but expression is of type integer
--
-- at terminal persist for a resolve_conflict run -- the only action_scope that ever
-- puts a non-NULL value in either column (admission.py). sqlite's own 105 already used
-- INTEGER and mysql's used TINYINT(1); only postgres drifted, so only postgres needs
-- this repair (see the sibling 115 files in sqlite/mysql, which change nothing).
--
-- 105 itself is not touched: it is already applied on the live database, and rewriting
-- an applied migration's file does not change the column type sqloader already
-- created there. This is a forward repair instead, run after 105 on every database
-- (deployed or fresh), converging both to the table's existing nullable-INTEGER-0/1
-- contract.
--
-- `USING col::integer` would also work here (PostgreSQL does support a direct
-- boolean->integer cast, both for literals and for a boolean column). The CASE below
-- is used anyway so the NULL/true/false -> NULL/1/0 mapping this repair promises is
-- explicit in the migration text rather than resting on cast semantics.

-- Each ALTER only fires while the column is still BOOLEAN. Without this guard a
-- manual re-run (the scenario the guard comment below already promises safety for)
-- would re-evaluate `CASE WHEN write_requested_by_human THEN ...` against a column
-- that is by then INTEGER, and PostgreSQL requires a CASE WHEN condition to be
-- boolean -- the second run would fail with "argument of CASE/WHEN must be type
-- boolean, not type integer" instead of being a no-op.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = 'ai_invoke_runs'
          AND column_name = 'write_requested_by_human'
          AND data_type = 'boolean'
    ) THEN
        ALTER TABLE ai_invoke_runs
            ALTER COLUMN write_requested_by_human TYPE INTEGER
            USING (CASE
                WHEN write_requested_by_human IS NULL THEN NULL
                WHEN write_requested_by_human THEN 1
                ELSE 0
            END);
    END IF;

    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = current_schema()
          AND table_name = 'ai_invoke_runs'
          AND column_name = 'allow_test_edits'
          AND data_type = 'boolean'
    ) THEN
        ALTER TABLE ai_invoke_runs
            ALTER COLUMN allow_test_edits TYPE INTEGER
            USING (CASE
                WHEN allow_test_edits IS NULL THEN NULL
                WHEN allow_test_edits THEN 1
                ELSE 0
            END);
    END IF;
END $$;

-- Guarded like 107/110's constraint repairs: this file runs exactly once per database
-- via sqloader's migrations ledger, but the guard keeps a manual re-run harmless rather
-- than raising "constraint already exists".
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint c
        JOIN pg_class t ON t.oid = c.conrelid
        JOIN pg_namespace n ON n.oid = t.relnamespace
        WHERE n.nspname = current_schema()
          AND t.relname = 'ai_invoke_runs'
          AND c.conname = 'ai_invoke_runs_write_requested_by_human_check'
    ) THEN
        ALTER TABLE ai_invoke_runs
            ADD CONSTRAINT ai_invoke_runs_write_requested_by_human_check
            CHECK (write_requested_by_human IS NULL OR write_requested_by_human IN (0, 1));
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint c
        JOIN pg_class t ON t.oid = c.conrelid
        JOIN pg_namespace n ON n.oid = t.relnamespace
        WHERE n.nspname = current_schema()
          AND t.relname = 'ai_invoke_runs'
          AND c.conname = 'ai_invoke_runs_allow_test_edits_check'
    ) THEN
        ALTER TABLE ai_invoke_runs
            ADD CONSTRAINT ai_invoke_runs_allow_test_edits_check
            CHECK (allow_test_edits IS NULL OR allow_test_edits IN (0, 1));
    END IF;
END $$;
