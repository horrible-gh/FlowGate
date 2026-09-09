-- 078b_seed_work_plan_doctype.sql
-- flowgate.default.0395 T0011 / D0007 §2.2 · §7, NR0005 §3.2: register the work plan
-- (WP) document type as a GLOBAL system type.
--
-- Type code decision (NR0005 §1, D0007 §0): 'WP' (Work Plan). The Korean name is
-- 작업계획. It is a general-series advisory document, not an instruction and not a
-- work result — the same series as M (memo) and CH (conversation), which is why it
-- carries no pair type and never appears in the instruction/work series.
--
-- WP is NOT auto-complete (D0007 §3.1 결정 4): it is created pending_review and goes
-- through the ordinary review pipeline, so it must not be added to AUTO_COMPLETE_TYPES.
--
-- This is a DATA insert (a global system type row), not a schema change
-- (NR0005 §6.1: DB design is 0 pages). Idempotent via INSERT, so rerunning is safe.

-- Global system type row (project_id=NULL, is_system=1). general series, like M/CH.
-- sort_order 30 places it right after CH (25) within the general series.
DO $fg_or_ignore$
BEGIN
INSERT INTO document_types
    (project_id, type_code, type_name, series, is_system, is_active, sort_order, created_at, updated_at)
VALUES
    (NULL, 'WP', '작업계획', 'general', 1, 1, 30, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP) ON CONFLICT DO NOTHING;
EXCEPTION WHEN check_violation OR not_null_violation OR foreign_key_violation THEN
    NULL;  -- OR IGNORE: drop the violating row(s), like SQLite/MySQL
END $fg_or_ignore$;
-- Localized display names (document_type_names: ko / ja / en), mirroring the CH seed.
DO $fg_or_ignore$
BEGIN
INSERT INTO document_type_names (document_type_id, locale, type_name)
SELECT id, 'ko', '작업계획'
FROM   document_types
WHERE  series = 'general' AND type_code = 'WP' AND project_id IS NULL ON CONFLICT DO NOTHING;
EXCEPTION WHEN check_violation OR not_null_violation OR foreign_key_violation THEN
    NULL;  -- OR IGNORE: drop the violating row(s), like SQLite/MySQL
END $fg_or_ignore$;
DO $fg_or_ignore$
BEGIN
INSERT INTO document_type_names (document_type_id, locale, type_name)
SELECT id, 'ja', '作業計画'
FROM   document_types
WHERE  series = 'general' AND type_code = 'WP' AND project_id IS NULL ON CONFLICT DO NOTHING;
EXCEPTION WHEN check_violation OR not_null_violation OR foreign_key_violation THEN
    NULL;  -- OR IGNORE: drop the violating row(s), like SQLite/MySQL
END $fg_or_ignore$;
DO $fg_or_ignore$
BEGIN
INSERT INTO document_type_names (document_type_id, locale, type_name)
SELECT id, 'en', 'Work Plan'
FROM   document_types
WHERE  series = 'general' AND type_code = 'WP' AND project_id IS NULL ON CONFLICT DO NOTHING;
EXCEPTION WHEN check_violation OR not_null_violation OR foreign_key_violation THEN
    NULL;  -- OR IGNORE: drop the violating row(s), like SQLite/MySQL
END $fg_or_ignore$;
-- Localized descriptions (document_type_descriptions, added by 074).
DO $fg_or_ignore$
BEGIN
INSERT INTO document_type_descriptions (document_type_id, locale, description)
SELECT id, 'ko', '설계 장수와 작업 세트수, 단계별 공급자를 미리 정해 두는 계획 문서. 실행을 강제하지 않는 자문형(advisory) 문서다.'
FROM   document_types WHERE series = 'general' AND type_code = 'WP' AND project_id IS NULL ON CONFLICT DO NOTHING;
EXCEPTION WHEN check_violation OR not_null_violation OR foreign_key_violation THEN
    NULL;  -- OR IGNORE: drop the violating row(s), like SQLite/MySQL
END $fg_or_ignore$;
DO $fg_or_ignore$
BEGIN
INSERT INTO document_type_descriptions (document_type_id, locale, description)
SELECT id, 'ja', '設計の枚数と作業セット数、段階ごとの提供者をあらかじめ決めておく計画文書。実行を強制しない助言型(advisory)の文書。'
FROM   document_types WHERE series = 'general' AND type_code = 'WP' AND project_id IS NULL ON CONFLICT DO NOTHING;
EXCEPTION WHEN check_violation OR not_null_violation OR foreign_key_violation THEN
    NULL;  -- OR IGNORE: drop the violating row(s), like SQLite/MySQL
END $fg_or_ignore$;
DO $fg_or_ignore$
BEGIN
INSERT INTO document_type_descriptions (document_type_id, locale, description)
SELECT id, 'en', 'Plan document fixing design sheet counts, work set counts, and the provider for each step. Advisory only — it never forces execution.'
FROM   document_types WHERE series = 'general' AND type_code = 'WP' AND project_id IS NULL ON CONFLICT DO NOTHING;
EXCEPTION WHEN check_violation OR not_null_violation OR foreign_key_violation THEN
    NULL;  -- OR IGNORE: drop the violating row(s), like SQLite/MySQL
END $fg_or_ignore$;
