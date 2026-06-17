-- 026_doctype_jaen_labels.sql
-- T479: apply ja/en labels to document_type_names (preserve ko labels)
-- Fix the issue where ja/en labels fall back to ko values after migration 025

PRAGMA foreign_keys = OFF;
BEGIN;

-- ── ja/en label UPDATE (21 type_codes × 2 languages = 42 UPDATE) ────────────────────

-- general series
-- R (Requirements)
UPDATE document_type_names
SET    type_name = '要件定義'
WHERE  locale = 'ja'
  AND  document_type_id = (
         SELECT id FROM document_types
         WHERE  series = 'general' AND type_code = 'R'
           AND  project_id IS NULL
       );

UPDATE document_type_names
SET    type_name = 'Requirements'
WHERE  locale = 'en'
  AND  document_type_id = (
         SELECT id FROM document_types
         WHERE  series = 'general' AND type_code = 'R'
           AND  project_id IS NULL
       );

-- M (Memo)
UPDATE document_type_names
SET    type_name = 'メモ'
WHERE  locale = 'ja'
  AND  document_type_id = (
         SELECT id FROM document_types
         WHERE  series = 'general' AND type_code = 'M'
           AND  project_id IS NULL
       );

UPDATE document_type_names
SET    type_name = 'Memo'
WHERE  locale = 'en'
  AND  document_type_id = (
         SELECT id FROM document_types
         WHERE  series = 'general' AND type_code = 'M'
           AND  project_id IS NULL
       );

-- Q (Question)
UPDATE document_type_names
SET    type_name = '質問'
WHERE  locale = 'ja'
  AND  document_type_id = (
         SELECT id FROM document_types
         WHERE  series = 'general' AND type_code = 'Q'
           AND  project_id IS NULL
       );

UPDATE document_type_names
SET    type_name = 'Question'
WHERE  locale = 'en'
  AND  document_type_id = (
         SELECT id FROM document_types
         WHERE  series = 'general' AND type_code = 'Q'
           AND  project_id IS NULL
       );

-- A (Answer)
UPDATE document_type_names
SET    type_name = '回答'
WHERE  locale = 'ja'
  AND  document_type_id = (
         SELECT id FROM document_types
         WHERE  series = 'general' AND type_code = 'A'
           AND  project_id IS NULL
       );

UPDATE document_type_names
SET    type_name = 'Answer'
WHERE  locale = 'en'
  AND  document_type_id = (
         SELECT id FROM document_types
         WHERE  series = 'general' AND type_code = 'A'
           AND  project_id IS NULL
       );

-- L (Log)
UPDATE document_type_names
SET    type_name = 'ログ'
WHERE  locale = 'ja'
  AND  document_type_id = (
         SELECT id FROM document_types
         WHERE  series = 'general' AND type_code = 'L'
           AND  project_id IS NULL
       );

UPDATE document_type_names
SET    type_name = 'Log'
WHERE  locale = 'en'
  AND  document_type_id = (
         SELECT id FROM document_types
         WHERE  series = 'general' AND type_code = 'L'
           AND  project_id IS NULL
       );

-- B (Bug)
UPDATE document_type_names
SET    type_name = 'バグ'
WHERE  locale = 'ja'
  AND  document_type_id = (
         SELECT id FROM document_types
         WHERE  series = 'general' AND type_code = 'B'
           AND  project_id IS NULL
       );

UPDATE document_type_names
SET    type_name = 'Bug'
WHERE  locale = 'en'
  AND  document_type_id = (
         SELECT id FROM document_types
         WHERE  series = 'general' AND type_code = 'B'
           AND  project_id IS NULL
       );

-- instruction series
-- DS (Design Instruction)
UPDATE document_type_names
SET    type_name = '設計指示'
WHERE  locale = 'ja'
  AND  document_type_id = (
         SELECT id FROM document_types
         WHERE  series = 'instruction' AND type_code = 'DS'
           AND  project_id IS NULL
       );

UPDATE document_type_names
SET    type_name = 'Design Instruction'
WHERE  locale = 'en'
  AND  document_type_id = (
         SELECT id FROM document_types
         WHERE  series = 'instruction' AND type_code = 'DS'
           AND  project_id IS NULL
       );

-- N (Investigation Instruction)
UPDATE document_type_names
SET    type_name = '調査指示'
WHERE  locale = 'ja'
  AND  document_type_id = (
         SELECT id FROM document_types
         WHERE  series = 'instruction' AND type_code = 'N'
           AND  project_id IS NULL
       );

UPDATE document_type_names
SET    type_name = 'Investigation Instruction'
WHERE  locale = 'en'
  AND  document_type_id = (
         SELECT id FROM document_types
         WHERE  series = 'instruction' AND type_code = 'N'
           AND  project_id IS NULL
       );

-- T (Task Instruction)
UPDATE document_type_names
SET    type_name = 'タスク指示'
WHERE  locale = 'ja'
  AND  document_type_id = (
         SELECT id FROM document_types
         WHERE  series = 'instruction' AND type_code = 'T'
           AND  project_id IS NULL
       );

UPDATE document_type_names
SET    type_name = 'Task Instruction'
WHERE  locale = 'en'
  AND  document_type_id = (
         SELECT id FROM document_types
         WHERE  series = 'instruction' AND type_code = 'T'
           AND  project_id IS NULL
       );

-- TS (Test Scenario Instruction)
UPDATE document_type_names
SET    type_name = 'テストシナリオ指示'
WHERE  locale = 'ja'
  AND  document_type_id = (
         SELECT id FROM document_types
         WHERE  series = 'instruction' AND type_code = 'TS'
           AND  project_id IS NULL
       );

UPDATE document_type_names
SET    type_name = 'Test Scenario Instruction'
WHERE  locale = 'en'
  AND  document_type_id = (
         SELECT id FROM document_types
         WHERE  series = 'instruction' AND type_code = 'TS'
           AND  project_id IS NULL
       );

-- design series
-- D (Design)
UPDATE document_type_names
SET    type_name = '基本設計'
WHERE  locale = 'ja'
  AND  document_type_id = (
         SELECT id FROM document_types
         WHERE  series = 'design' AND type_code = 'D'
           AND  project_id IS NULL
       );

UPDATE document_type_names
SET    type_name = 'Basic Design'
WHERE  locale = 'en'
  AND  document_type_id = (
         SELECT id FROM document_types
         WHERE  series = 'design' AND type_code = 'D'
           AND  project_id IS NULL
       );

-- P (Protocol)
UPDATE document_type_names
SET    type_name = 'プロトコル'
WHERE  locale = 'ja'
  AND  document_type_id = (
         SELECT id FROM document_types
         WHERE  series = 'design' AND type_code = 'P'
           AND  project_id IS NULL
       );

UPDATE document_type_names
SET    type_name = 'Protocol Design'
WHERE  locale = 'en'
  AND  document_type_id = (
         SELECT id FROM document_types
         WHERE  series = 'design' AND type_code = 'P'
           AND  project_id IS NULL
       );

-- L (Logic)
UPDATE document_type_names
SET    type_name = 'ロジック'
WHERE  locale = 'ja'
  AND  document_type_id = (
         SELECT id FROM document_types
         WHERE  series = 'design' AND type_code = 'L'
           AND  project_id IS NULL
       );

UPDATE document_type_names
SET    type_name = 'Logic Design'
WHERE  locale = 'en'
  AND  document_type_id = (
         SELECT id FROM document_types
         WHERE  series = 'design' AND type_code = 'L'
           AND  project_id IS NULL
       );

-- DB (Database)
UPDATE document_type_names
SET    type_name = 'データベース'
WHERE  locale = 'ja'
  AND  document_type_id = (
         SELECT id FROM document_types
         WHERE  series = 'design' AND type_code = 'DB'
           AND  project_id IS NULL
       );

UPDATE document_type_names
SET    type_name = 'Database Design'
WHERE  locale = 'en'
  AND  document_type_id = (
         SELECT id FROM document_types
         WHERE  series = 'design' AND type_code = 'DB'
           AND  project_id IS NULL
       );

-- work series
-- NR (Investigation Report)
UPDATE document_type_names
SET    type_name = '調査レポート'
WHERE  locale = 'ja'
  AND  document_type_id = (
         SELECT id FROM document_types
         WHERE  series = 'work' AND type_code = 'NR'
           AND  project_id IS NULL
       );

UPDATE document_type_names
SET    type_name = 'Investigation Report'
WHERE  locale = 'en'
  AND  document_type_id = (
         SELECT id FROM document_types
         WHERE  series = 'work' AND type_code = 'NR'
           AND  project_id IS NULL
       );

-- TR (Task Report)
UPDATE document_type_names
SET    type_name = 'タスクレポート'
WHERE  locale = 'ja'
  AND  document_type_id = (
         SELECT id FROM document_types
         WHERE  series = 'work' AND type_code = 'TR'
           AND  project_id IS NULL
       );

UPDATE document_type_names
SET    type_name = 'Task Report'
WHERE  locale = 'en'
  AND  document_type_id = (
         SELECT id FROM document_types
         WHERE  series = 'work' AND type_code = 'TR'
           AND  project_id IS NULL
       );

-- TSR (Test Report)
UPDATE document_type_names
SET    type_name = 'テストレポート'
WHERE  locale = 'ja'
  AND  document_type_id = (
         SELECT id FROM document_types
         WHERE  series = 'work' AND type_code = 'TSR'
           AND  project_id IS NULL
       );

UPDATE document_type_names
SET    type_name = 'Test Report'
WHERE  locale = 'en'
  AND  document_type_id = (
         SELECT id FROM document_types
         WHERE  series = 'work' AND type_code = 'TSR'
           AND  project_id IS NULL
       );

-- V (Review Request)
UPDATE document_type_names
SET    type_name = 'レビュー依頼'
WHERE  locale = 'ja'
  AND  document_type_id = (
         SELECT id FROM document_types
         WHERE  series = 'work' AND type_code = 'V'
           AND  project_id IS NULL
       );

UPDATE document_type_names
SET    type_name = 'Review Request'
WHERE  locale = 'en'
  AND  document_type_id = (
         SELECT id FROM document_types
         WHERE  series = 'work' AND type_code = 'V'
           AND  project_id IS NULL
       );

-- C (Commit)
UPDATE document_type_names
SET    type_name = 'コミット'
WHERE  locale = 'ja'
  AND  document_type_id = (
         SELECT id FROM document_types
         WHERE  series = 'work' AND type_code = 'C'
           AND  project_id IS NULL
       );

UPDATE document_type_names
SET    type_name = 'Commit'
WHERE  locale = 'en'
  AND  document_type_id = (
         SELECT id FROM document_types
         WHERE  series = 'work' AND type_code = 'C'
           AND  project_id IS NULL
       );

-- action series
-- AC (Approval)
UPDATE document_type_names
SET    type_name = '承認'
WHERE  locale = 'ja'
  AND  document_type_id = (
         SELECT id FROM document_types
         WHERE  series = 'action' AND type_code = 'AC'
           AND  project_id IS NULL
       );

UPDATE document_type_names
SET    type_name = 'Approval'
WHERE  locale = 'en'
  AND  document_type_id = (
         SELECT id FROM document_types
         WHERE  series = 'action' AND type_code = 'AC'
           AND  project_id IS NULL
       );

-- RJ (Rejection)
UPDATE document_type_names
SET    type_name = '差し戻し'
WHERE  locale = 'ja'
  AND  document_type_id = (
         SELECT id FROM document_types
         WHERE  series = 'action' AND type_code = 'RJ'
           AND  project_id IS NULL
       );

UPDATE document_type_names
SET    type_name = 'Rejection'
WHERE  locale = 'en'
  AND  document_type_id = (
         SELECT id FROM document_types
         WHERE  series = 'action' AND type_code = 'RJ'
           AND  project_id IS NULL
       );

COMMIT;
PRAGMA foreign_keys = ON;
