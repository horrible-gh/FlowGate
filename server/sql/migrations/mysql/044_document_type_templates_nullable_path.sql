SET FOREIGN_KEY_CHECKS=0;
-- 044_document_type_templates_nullable_path.sql
-- flowgate.default.0024.0015-T  (design: DB0014 §2-4 / §3-2)
-- Stage 3-2: relax document_type_templates.template_path from NOT NULL to nullable,
--   so JSON body registration (P0011 E4) can create a registry row without a file
--   path (the D안 stores the body in document_type_template_contents, not on disk).
--
-- DB0014 §2-4 sanctioned either dropping the column OR "at minimum, removing the
-- NOT NULL constraint". We take the latter: the column is retained (nullable)
-- because an orthogonal Jinja document-render system (documents/template_service.py)
-- still reads template_path for *document* rendering — a feature outside this group's
-- scope. Path non-exposure (R0001 / AC-1) is delivered at the provision layer:
-- the :2713 Next-Step line and the G1/G2 provision API never read or emit a path.
--
-- SQLite cannot ALTER a column's NOT NULL, so the table is recreated (mirrors the
-- 025_document_type_names.sql procedure). Existing rows (id/project_id/type_code/
-- is_active/uploaded_*) are preserved with their id values, so the CASCADE FK from
-- 043's document_type_template_contents.template_id stays intact.
--
-- Also adds the partial unique index uq_dtt_global_type (DB0014 §2-3): SQLite treats
-- NULLs as distinct in UNIQUE(project_id, type_code), so the global-template invariant
-- "≤1 row per type_code where project_id IS NULL" is enforced here as a DB invariant.

-- ── 1. New body: template_path is now nullable (rest is the 001 SOT, verbatim) ──
CREATE TABLE document_type_templates__new (
    id              INTEGER PRIMARY KEY AUTO_INCREMENT,
    project_id      VARCHAR(191) REFERENCES projects(project_id) ON DELETE CASCADE,
    type_code       VARCHAR(191) NOT NULL,
    template_path   TEXT,                          -- was NOT NULL; now nullable (D안 body lives in contents)
    is_active       INTEGER NOT NULL DEFAULT 1,
    uploaded_by     VARCHAR(191) REFERENCES users(user_id),
    uploaded_at     TEXT NOT NULL,
    UNIQUE (project_id, type_code)
);
-- ── 2. Copy every row (id preserved → 043 contents FK stays valid) ─────────────
INSERT INTO document_type_templates__new
    (id, project_id, type_code, template_path, is_active, uploaded_by, uploaded_at)
SELECT id, project_id, type_code, template_path, is_active, uploaded_by, uploaded_at
FROM document_type_templates;
-- ── 3. Swap ───────────────────────────────────────────────────────────────────
DROP TABLE document_type_templates;
ALTER TABLE document_type_templates__new RENAME TO document_type_templates;
-- ── 4. Recreate the 001 index + add the global-type partial unique index ───────
CREATE INDEX IF NOT EXISTS idx_dtt_project_type
    ON document_type_templates(project_id, type_code);
-- NOTE: if the live DB already holds duplicate global rows (same type_code,
-- project_id IS NULL), this index creation fails — dedupe first (DB0014 §3-3).
ALTER TABLE document_type_templates ADD COLUMN IF NOT EXISTS _g_uq_dtt_global_type VARCHAR(255) GENERATED ALWAYS AS (CASE WHEN project_id IS NULL THEN CONCAT_WS(CHAR(31), type_code) ELSE NULL END) STORED;
CREATE UNIQUE INDEX IF NOT EXISTS uq_dtt_global_type ON document_type_templates (_g_uq_dtt_global_type);
SET FOREIGN_KEY_CHECKS=1;
