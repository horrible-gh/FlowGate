-- ═══════════════════════════════════════════════════════════════════════════
-- T155: DB consolidation setup — sqloader case + migration reinforcement
-- ═══════════════════════════════════════════════════════════════════════════
-- Main tasks:
-- 1. Add replacement columns/fields for deprecated tables
-- 2. Check for and add D009-missing tables/indexes/FKs
-- 3. Do not write data migration queries (development stage, unnecessary)
-- ═══════════════════════════════════════════════════════════════════════════

-- ─────────────────────────────────────────────────────────────────────────
-- 1. Reinforce the documents table
-- Handling deprecated tables:
--   - tv_status -> absorbed into documents.status + documents.meta (JSON)
--   - tv_clear_scope -> reinforce the documents.meta (JSON) field
-- ─────────────────────────────────────────────────────────────────────────

-- Confirm the existing definition so documents.meta can hold TV progress metadata as JSON
-- (The table already has a meta column. This documents the format.)
-- meta JSON schema (for reference):
-- {
--   `tv_clear_scope`: { "... TV clear scope data ..." },
--   `tv_progress`: "...",
--   `tv_baseline_doc_id`: "...",
--   `tv_re_run_reason`: "..."
-- }

-- ─────────────────────────────────────────────────────────────────────────
-- 2. Normalize document status values (if needed)
-- D009: 'draft','open','in_review','approved','rejected','cancelled','closed','archived'
-- Current state: same (CHECK constraint already applied)
-- ─────────────────────────────────────────────────────────────────────────
-- (Already defined. No additional work needed.)

-- ─────────────────────────────────────────────────────────────────────────
-- 3. Reinforce the events table
-- Handle tv_clear_scope deprecation: record all state transitions in the events table
-- ─────────────────────────────────────────────────────────────────────────
-- (Already defined. actor_user_id FK exists. No additional work needed.)

-- ─────────────────────────────────────────────────────────────────────────
-- 4. Consolidate id_counter (group_sequences deprecated)
-- id_counter is already designed to include all group_sequences functionality
-- ─────────────────────────────────────────────────────────────────────────
-- (Already consolidated. No additional work needed.)

-- ─────────────────────────────────────────────────────────────────────────
-- 5. Reinforce the projects table (allowed_projects deprecated)
-- Replacement for allowed_projects: use the projects table
-- ─────────────────────────────────────────────────────────────────────────
-- (Already defined. No additional work needed.)

-- ─────────────────────────────────────────────────────────────────────────
-- 6. Check for and add missing tables (21 D009 tables)
-- ─────────────────────────────────────────────────────────────────────────

-- D009 required tables (verified):
-- 1. users ✓
-- 2. roles ✓
-- 3. permissions ✓
-- 4. role_permissions ✓
-- 5. user_project_roles ✓
-- 6. projects ✓
-- 7. project_settings ✓
-- 8. groups ✓
-- 9. sub_groups ✓
-- 10. id_counter ✓
-- 11. document_types ✓
-- 12. document_type_templates ✓
-- 13. documents ✓
-- 14. events ✓
-- 15. tv_scenarios ✓
-- 16. token_blacklist ✓
-- 17. refresh_tokens ✓
-- 18. system_settings ✓
-- 19. numbering_jobs ✓
-- 20. workflow_events ✓
-- 21. (VIEW) v_tv_progress ✓
-- 22. (VIEW) v_tv_open ✓

-- ─────────────────────────────────────────────────────────────────────────
-- 7. Add missing columns (cross-check current state vs D009)
-- ─────────────────────────────────────────────────────────────────────────

-- 7-1. users table: add 2FA / login security fields
-- (SQLite does not support IF NOT EXISTS for ALTER TABLE ADD COLUMN)
-- (Already added in 501_auth_columns.sql)
-- ALTER TABLE users ADD COLUMN totp_failed_count INTEGER NOT NULL DEFAULT 0;
-- ALTER TABLE users ADD COLUMN totp_locked_until TEXT;
-- ALTER TABLE users ADD COLUMN login_failed_count INTEGER NOT NULL DEFAULT 0;
-- ALTER TABLE users ADD COLUMN login_locked_until TEXT;

-- 7-2. projects table: add UI/color fields
-- (Already added in 006_add_color_to_projects.sql)
-- ALTER TABLE projects ADD COLUMN color TEXT;

-- 7-3. workflow_events table: check for missing fields (refer to the current schema)
-- (All fields are already defined.)

-- ─────────────────────────────────────────────────────────────────────────
-- 8. Check index optimization
-- ─────────────────────────────────────────────────────────────────────────
-- (All required indexes are defined in 001_flowgate_schema.sql)

-- ─────────────────────────────────────────────────────────────────────────
-- 9. Normalize constraints
-- ─────────────────────────────────────────────────────────────────────────

-- Referential integrity: all FKs are defined in 001_flowgate_schema.sql
-- Uniqueness: all UNIQUE constraints are defined in 001_flowgate_schema.sql

-- ─────────────────────────────────────────────────────────────────────────
-- 10. Record the migration mark (handled automatically by the migrations table)
-- ─────────────────────────────────────────────────────────────────────────
-- sqloader automatically records this in the migrations table. No additional work needed.

