SET FOREIGN_KEY_CHECKS=0;
-- 025_document_type_names.sql
-- T476: add document_type_names + explicit ko/ja/en seed INSERTs
--       remove the document_types.type_name / locale columns
-- PM decision: ISO 639-1 locales (ko, ja, en), fallback = ko (ja/en undecided)

-- ── 1. Create the new table ──────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS document_type_names (
    document_type_id INTEGER NOT NULL
        REFERENCES document_types(id) ON DELETE CASCADE,
    locale           VARCHAR(191)    NOT NULL,   -- ISO 639-1: ko / ja / en
    type_name        TEXT    NOT NULL,
    PRIMARY KEY (document_type_id, locale)
);
CREATE INDEX IF NOT EXISTS idx_dtn_locale ON document_type_names(locale);
-- ── 2. Explicit seed INSERTs (ko / ja / en × 21 system types) ───────────────
-- general series (R, M, Q, A, L, B)
INSERT IGNORE INTO document_type_names (document_type_id, locale, type_name)
SELECT id, 'ko', '요건정의'
FROM   document_types
WHERE  series = 'general' AND type_code = 'R' AND project_id IS NULL;
INSERT IGNORE INTO document_type_names (document_type_id, locale, type_name)
SELECT id, 'ja', '요건정의'
FROM   document_types
WHERE  series = 'general' AND type_code = 'R' AND project_id IS NULL;
INSERT IGNORE INTO document_type_names (document_type_id, locale, type_name)
SELECT id, 'en', '요건정의'
FROM   document_types
WHERE  series = 'general' AND type_code = 'R' AND project_id IS NULL;
INSERT IGNORE INTO document_type_names (document_type_id, locale, type_name)
SELECT id, 'ko', '메모'
FROM   document_types
WHERE  series = 'general' AND type_code = 'M' AND project_id IS NULL;
INSERT IGNORE INTO document_type_names (document_type_id, locale, type_name)
SELECT id, 'ja', '메모'
FROM   document_types
WHERE  series = 'general' AND type_code = 'M' AND project_id IS NULL;
INSERT IGNORE INTO document_type_names (document_type_id, locale, type_name)
SELECT id, 'en', '메모'
FROM   document_types
WHERE  series = 'general' AND type_code = 'M' AND project_id IS NULL;
INSERT IGNORE INTO document_type_names (document_type_id, locale, type_name)
SELECT id, 'ko', '질의'
FROM   document_types
WHERE  series = 'general' AND type_code = 'Q' AND project_id IS NULL;
INSERT IGNORE INTO document_type_names (document_type_id, locale, type_name)
SELECT id, 'ja', '질의'
FROM   document_types
WHERE  series = 'general' AND type_code = 'Q' AND project_id IS NULL;
INSERT IGNORE INTO document_type_names (document_type_id, locale, type_name)
SELECT id, 'en', '질의'
FROM   document_types
WHERE  series = 'general' AND type_code = 'Q' AND project_id IS NULL;
INSERT IGNORE INTO document_type_names (document_type_id, locale, type_name)
SELECT id, 'ko', '응답'
FROM   document_types
WHERE  series = 'general' AND type_code = 'A' AND project_id IS NULL;
INSERT IGNORE INTO document_type_names (document_type_id, locale, type_name)
SELECT id, 'ja', '응답'
FROM   document_types
WHERE  series = 'general' AND type_code = 'A' AND project_id IS NULL;
INSERT IGNORE INTO document_type_names (document_type_id, locale, type_name)
SELECT id, 'en', '응답'
FROM   document_types
WHERE  series = 'general' AND type_code = 'A' AND project_id IS NULL;
INSERT IGNORE INTO document_type_names (document_type_id, locale, type_name)
SELECT id, 'ko', '로그'
FROM   document_types
WHERE  series = 'general' AND type_code = 'L' AND project_id IS NULL;
INSERT IGNORE INTO document_type_names (document_type_id, locale, type_name)
SELECT id, 'ja', '로그'
FROM   document_types
WHERE  series = 'general' AND type_code = 'L' AND project_id IS NULL;
INSERT IGNORE INTO document_type_names (document_type_id, locale, type_name)
SELECT id, 'en', '로그'
FROM   document_types
WHERE  series = 'general' AND type_code = 'L' AND project_id IS NULL;
INSERT IGNORE INTO document_type_names (document_type_id, locale, type_name)
SELECT id, 'ko', '버그'
FROM   document_types
WHERE  series = 'general' AND type_code = 'B' AND project_id IS NULL;
INSERT IGNORE INTO document_type_names (document_type_id, locale, type_name)
SELECT id, 'ja', '버그'
FROM   document_types
WHERE  series = 'general' AND type_code = 'B' AND project_id IS NULL;
INSERT IGNORE INTO document_type_names (document_type_id, locale, type_name)
SELECT id, 'en', '버그'
FROM   document_types
WHERE  series = 'general' AND type_code = 'B' AND project_id IS NULL;
-- instruction series (DS, N, T, TS)
INSERT IGNORE INTO document_type_names (document_type_id, locale, type_name)
SELECT id, 'ko', '설계지시'
FROM   document_types
WHERE  series = 'instruction' AND type_code = 'DS' AND project_id IS NULL;
INSERT IGNORE INTO document_type_names (document_type_id, locale, type_name)
SELECT id, 'ja', '설계지시'
FROM   document_types
WHERE  series = 'instruction' AND type_code = 'DS' AND project_id IS NULL;
INSERT IGNORE INTO document_type_names (document_type_id, locale, type_name)
SELECT id, 'en', '설계지시'
FROM   document_types
WHERE  series = 'instruction' AND type_code = 'DS' AND project_id IS NULL;
INSERT IGNORE INTO document_type_names (document_type_id, locale, type_name)
SELECT id, 'ko', '조사지시'
FROM   document_types
WHERE  series = 'instruction' AND type_code = 'N' AND project_id IS NULL;
INSERT IGNORE INTO document_type_names (document_type_id, locale, type_name)
SELECT id, 'ja', '조사지시'
FROM   document_types
WHERE  series = 'instruction' AND type_code = 'N' AND project_id IS NULL;
INSERT IGNORE INTO document_type_names (document_type_id, locale, type_name)
SELECT id, 'en', '조사지시'
FROM   document_types
WHERE  series = 'instruction' AND type_code = 'N' AND project_id IS NULL;
INSERT IGNORE INTO document_type_names (document_type_id, locale, type_name)
SELECT id, 'ko', '작업지시'
FROM   document_types
WHERE  series = 'instruction' AND type_code = 'T' AND project_id IS NULL;
INSERT IGNORE INTO document_type_names (document_type_id, locale, type_name)
SELECT id, 'ja', '작업지시'
FROM   document_types
WHERE  series = 'instruction' AND type_code = 'T' AND project_id IS NULL;
INSERT IGNORE INTO document_type_names (document_type_id, locale, type_name)
SELECT id, 'en', '작업지시'
FROM   document_types
WHERE  series = 'instruction' AND type_code = 'T' AND project_id IS NULL;
INSERT IGNORE INTO document_type_names (document_type_id, locale, type_name)
SELECT id, 'ko', '테스트시나리오지시'
FROM   document_types
WHERE  series = 'instruction' AND type_code = 'TS' AND project_id IS NULL;
INSERT IGNORE INTO document_type_names (document_type_id, locale, type_name)
SELECT id, 'ja', '테스트시나리오지시'
FROM   document_types
WHERE  series = 'instruction' AND type_code = 'TS' AND project_id IS NULL;
INSERT IGNORE INTO document_type_names (document_type_id, locale, type_name)
SELECT id, 'en', '테스트시나리오지시'
FROM   document_types
WHERE  series = 'instruction' AND type_code = 'TS' AND project_id IS NULL;
-- design series (D, P, L, DB)
INSERT IGNORE INTO document_type_names (document_type_id, locale, type_name)
SELECT id, 'ko', '기본설계'
FROM   document_types
WHERE  series = 'design' AND type_code = 'D' AND project_id IS NULL;
INSERT IGNORE INTO document_type_names (document_type_id, locale, type_name)
SELECT id, 'ja', '기본설계'
FROM   document_types
WHERE  series = 'design' AND type_code = 'D' AND project_id IS NULL;
INSERT IGNORE INTO document_type_names (document_type_id, locale, type_name)
SELECT id, 'en', '기본설계'
FROM   document_types
WHERE  series = 'design' AND type_code = 'D' AND project_id IS NULL;
INSERT IGNORE INTO document_type_names (document_type_id, locale, type_name)
SELECT id, 'ko', '프로토콜'
FROM   document_types
WHERE  series = 'design' AND type_code = 'P' AND project_id IS NULL;
INSERT IGNORE INTO document_type_names (document_type_id, locale, type_name)
SELECT id, 'ja', '프로토콜'
FROM   document_types
WHERE  series = 'design' AND type_code = 'P' AND project_id IS NULL;
INSERT IGNORE INTO document_type_names (document_type_id, locale, type_name)
SELECT id, 'en', '프로토콜'
FROM   document_types
WHERE  series = 'design' AND type_code = 'P' AND project_id IS NULL;
INSERT IGNORE INTO document_type_names (document_type_id, locale, type_name)
SELECT id, 'ko', '로직'
FROM   document_types
WHERE  series = 'design' AND type_code = 'L' AND project_id IS NULL;
INSERT IGNORE INTO document_type_names (document_type_id, locale, type_name)
SELECT id, 'ja', '로직'
FROM   document_types
WHERE  series = 'design' AND type_code = 'L' AND project_id IS NULL;
INSERT IGNORE INTO document_type_names (document_type_id, locale, type_name)
SELECT id, 'en', '로직'
FROM   document_types
WHERE  series = 'design' AND type_code = 'L' AND project_id IS NULL;
INSERT IGNORE INTO document_type_names (document_type_id, locale, type_name)
SELECT id, 'ko', '데이터베이스'
FROM   document_types
WHERE  series = 'design' AND type_code = 'DB' AND project_id IS NULL;
INSERT IGNORE INTO document_type_names (document_type_id, locale, type_name)
SELECT id, 'ja', '데이터베이스'
FROM   document_types
WHERE  series = 'design' AND type_code = 'DB' AND project_id IS NULL;
INSERT IGNORE INTO document_type_names (document_type_id, locale, type_name)
SELECT id, 'en', '데이터베이스'
FROM   document_types
WHERE  series = 'design' AND type_code = 'DB' AND project_id IS NULL;
-- work series (NR, TR, TSR, V, C)
INSERT IGNORE INTO document_type_names (document_type_id, locale, type_name)
SELECT id, 'ko', '조사레포트'
FROM   document_types
WHERE  series = 'work' AND type_code = 'NR' AND project_id IS NULL;
INSERT IGNORE INTO document_type_names (document_type_id, locale, type_name)
SELECT id, 'ja', '조사레포트'
FROM   document_types
WHERE  series = 'work' AND type_code = 'NR' AND project_id IS NULL;
INSERT IGNORE INTO document_type_names (document_type_id, locale, type_name)
SELECT id, 'en', '조사레포트'
FROM   document_types
WHERE  series = 'work' AND type_code = 'NR' AND project_id IS NULL;
INSERT IGNORE INTO document_type_names (document_type_id, locale, type_name)
SELECT id, 'ko', '작업레포트'
FROM   document_types
WHERE  series = 'work' AND type_code = 'TR' AND project_id IS NULL;
INSERT IGNORE INTO document_type_names (document_type_id, locale, type_name)
SELECT id, 'ja', '작업레포트'
FROM   document_types
WHERE  series = 'work' AND type_code = 'TR' AND project_id IS NULL;
INSERT IGNORE INTO document_type_names (document_type_id, locale, type_name)
SELECT id, 'en', '작업레포트'
FROM   document_types
WHERE  series = 'work' AND type_code = 'TR' AND project_id IS NULL;
INSERT IGNORE INTO document_type_names (document_type_id, locale, type_name)
SELECT id, 'ko', '테스트레포트'
FROM   document_types
WHERE  series = 'work' AND type_code = 'TSR' AND project_id IS NULL;
INSERT IGNORE INTO document_type_names (document_type_id, locale, type_name)
SELECT id, 'ja', '테스트레포트'
FROM   document_types
WHERE  series = 'work' AND type_code = 'TSR' AND project_id IS NULL;
INSERT IGNORE INTO document_type_names (document_type_id, locale, type_name)
SELECT id, 'en', '테스트레포트'
FROM   document_types
WHERE  series = 'work' AND type_code = 'TSR' AND project_id IS NULL;
INSERT IGNORE INTO document_type_names (document_type_id, locale, type_name)
SELECT id, 'ko', '리뷰의뢰'
FROM   document_types
WHERE  series = 'work' AND type_code = 'V' AND project_id IS NULL;
INSERT IGNORE INTO document_type_names (document_type_id, locale, type_name)
SELECT id, 'ja', '리뷰의뢰'
FROM   document_types
WHERE  series = 'work' AND type_code = 'V' AND project_id IS NULL;
INSERT IGNORE INTO document_type_names (document_type_id, locale, type_name)
SELECT id, 'en', '리뷰의뢰'
FROM   document_types
WHERE  series = 'work' AND type_code = 'V' AND project_id IS NULL;
INSERT IGNORE INTO document_type_names (document_type_id, locale, type_name)
SELECT id, 'ko', '커밋'
FROM   document_types
WHERE  series = 'work' AND type_code = 'C' AND project_id IS NULL;
INSERT IGNORE INTO document_type_names (document_type_id, locale, type_name)
SELECT id, 'ja', '커밋'
FROM   document_types
WHERE  series = 'work' AND type_code = 'C' AND project_id IS NULL;
INSERT IGNORE INTO document_type_names (document_type_id, locale, type_name)
SELECT id, 'en', '커밋'
FROM   document_types
WHERE  series = 'work' AND type_code = 'C' AND project_id IS NULL;
-- action series (AC, RJ)
INSERT IGNORE INTO document_type_names (document_type_id, locale, type_name)
SELECT id, 'ko', '승인'
FROM   document_types
WHERE  series = 'action' AND type_code = 'AC' AND project_id IS NULL;
INSERT IGNORE INTO document_type_names (document_type_id, locale, type_name)
SELECT id, 'ja', '승인'
FROM   document_types
WHERE  series = 'action' AND type_code = 'AC' AND project_id IS NULL;
INSERT IGNORE INTO document_type_names (document_type_id, locale, type_name)
SELECT id, 'en', '승인'
FROM   document_types
WHERE  series = 'action' AND type_code = 'AC' AND project_id IS NULL;
INSERT IGNORE INTO document_type_names (document_type_id, locale, type_name)
SELECT id, 'ko', '반려'
FROM   document_types
WHERE  series = 'action' AND type_code = 'RJ' AND project_id IS NULL;
INSERT IGNORE INTO document_type_names (document_type_id, locale, type_name)
SELECT id, 'ja', '반려'
FROM   document_types
WHERE  series = 'action' AND type_code = 'RJ' AND project_id IS NULL;
INSERT IGNORE INTO document_type_names (document_type_id, locale, type_name)
SELECT id, 'en', '반려'
FROM   document_types
WHERE  series = 'action' AND type_code = 'RJ' AND project_id IS NULL;
-- ── 3. Recreate document_types (SQLite does not support DROP COLUMN, so recreation is required) ─────
-- Recreate only after checking whether the type_name column exists (idempotent)
CREATE TABLE document_types_new (
    id           INTEGER PRIMARY KEY AUTO_INCREMENT,
    project_id   VARCHAR(191) REFERENCES projects(project_id) ON DELETE CASCADE,
    type_code    VARCHAR(191) NOT NULL,
    series       VARCHAR(191) NOT NULL
                     CHECK (series IN ('general', 'instruction', 'design', 'work', 'action')),
    is_system    INTEGER NOT NULL DEFAULT 0,
    is_active    INTEGER NOT NULL DEFAULT 1,
    sort_order   INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    color        TEXT,
    description  TEXT
    -- type_name and locale columns removed
);
INSERT INTO document_types_new
    (id, project_id, type_code, series,
     is_system, is_active, sort_order, created_at, updated_at,
     color, description)
SELECT
    id, project_id, type_code, series,
    is_system, is_active, sort_order, created_at, updated_at,
    color, description
FROM document_types;
DROP TABLE document_types;
ALTER TABLE document_types_new RENAME TO document_types;
-- ── 4. Recreate indexes ──────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_doc_types_project  ON document_types(project_id);
CREATE INDEX IF NOT EXISTS idx_doc_types_series   ON document_types(series);
CREATE INDEX IF NOT EXISTS idx_doc_types_active   ON document_types(is_active);
ALTER TABLE document_types ADD COLUMN IF NOT EXISTS _g_ux_doc_types_global VARCHAR(255) GENERATED ALWAYS AS (CASE WHEN project_id IS NULL THEN CONCAT_WS(CHAR(31), series, type_code) ELSE NULL END) STORED;
CREATE UNIQUE INDEX IF NOT EXISTS ux_doc_types_global ON document_types (_g_ux_doc_types_global);
CREATE UNIQUE INDEX IF NOT EXISTS ux_doc_types_project ON document_types (project_id, series, type_code);
SET FOREIGN_KEY_CHECKS=1;
