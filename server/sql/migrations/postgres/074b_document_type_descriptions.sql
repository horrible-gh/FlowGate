-- 074b_document_type_descriptions.sql
-- T0019: store the type description text (help/doc_type "description" field) per
-- locale instead of the single ko-only document_types.description/locale columns
-- (T391/022), and fill in ja/en translations. Mirrors the document_type_names
-- (025/026) pattern: one row per (document_type_id, locale), ko/ja/en fallback
-- order resolved by the caller (db/templates.list_document_types).


CREATE TABLE IF NOT EXISTS document_type_descriptions (
    document_type_id INTEGER NOT NULL
        REFERENCES document_types(id) ON DELETE CASCADE,
    locale            TEXT    NOT NULL,   -- ISO 639-1: ko / ja / en
    description       TEXT    NOT NULL,
    PRIMARY KEY (document_type_id, locale)
);
CREATE INDEX IF NOT EXISTS idx_dtd_locale ON document_type_descriptions(locale);

-- ── 1. Carry over the existing ko text from document_types.description ─────
DO $fg_or_ignore$
BEGIN
INSERT INTO document_type_descriptions (document_type_id, locale, description)
SELECT id, 'ko', description
FROM   document_types
WHERE  description IS NOT NULL AND project_id IS NULL ON CONFLICT DO NOTHING;
EXCEPTION WHEN check_violation OR not_null_violation OR foreign_key_violation THEN
    NULL;  -- OR IGNORE: drop the violating row(s), like SQLite/MySQL
END $fg_or_ignore$;

-- ── 2. ko/ja/en seed per system type (general series: R, M, Q, A, L, B, CH) ─
DO $fg_or_ignore$
BEGIN
INSERT INTO document_type_descriptions (document_type_id, locale, description)
SELECT id, 'ko', '무엇을·왜 만들지를 정의하는 문서. 기능·비기능 요구사항을 정리한다.'
FROM   document_types WHERE series = 'general' AND type_code = 'R' AND project_id IS NULL ON CONFLICT DO NOTHING;
EXCEPTION WHEN check_violation OR not_null_violation OR foreign_key_violation THEN
    NULL;  -- OR IGNORE: drop the violating row(s), like SQLite/MySQL
END $fg_or_ignore$;
DO $fg_or_ignore$
BEGIN
INSERT INTO document_type_descriptions (document_type_id, locale, description)
SELECT id, 'ja', '何を・なぜ作るかを定義する文書。機能・非機能要件を整理する。'
FROM   document_types WHERE series = 'general' AND type_code = 'R' AND project_id IS NULL ON CONFLICT DO NOTHING;
EXCEPTION WHEN check_violation OR not_null_violation OR foreign_key_violation THEN
    NULL;  -- OR IGNORE: drop the violating row(s), like SQLite/MySQL
END $fg_or_ignore$;
DO $fg_or_ignore$
BEGIN
INSERT INTO document_type_descriptions (document_type_id, locale, description)
SELECT id, 'en', 'Defines what to build and why. Captures functional and non-functional requirements.'
FROM   document_types WHERE series = 'general' AND type_code = 'R' AND project_id IS NULL ON CONFLICT DO NOTHING;
EXCEPTION WHEN check_violation OR not_null_violation OR foreign_key_violation THEN
    NULL;  -- OR IGNORE: drop the violating row(s), like SQLite/MySQL
END $fg_or_ignore$;

DO $fg_or_ignore$
BEGIN
INSERT INTO document_type_descriptions (document_type_id, locale, description)
SELECT id, 'ko', '결정사항·아이디어·중간 기록용 자유 형식 문서.'
FROM   document_types WHERE series = 'general' AND type_code = 'M' AND project_id IS NULL ON CONFLICT DO NOTHING;
EXCEPTION WHEN check_violation OR not_null_violation OR foreign_key_violation THEN
    NULL;  -- OR IGNORE: drop the violating row(s), like SQLite/MySQL
END $fg_or_ignore$;
DO $fg_or_ignore$
BEGIN
INSERT INTO document_type_descriptions (document_type_id, locale, description)
SELECT id, 'ja', '決定事項・アイデア・中間記録用の自由形式の文書。'
FROM   document_types WHERE series = 'general' AND type_code = 'M' AND project_id IS NULL ON CONFLICT DO NOTHING;
EXCEPTION WHEN check_violation OR not_null_violation OR foreign_key_violation THEN
    NULL;  -- OR IGNORE: drop the violating row(s), like SQLite/MySQL
END $fg_or_ignore$;
DO $fg_or_ignore$
BEGIN
INSERT INTO document_type_descriptions (document_type_id, locale, description)
SELECT id, 'en', 'Free-form document for decisions, ideas, and interim notes.'
FROM   document_types WHERE series = 'general' AND type_code = 'M' AND project_id IS NULL ON CONFLICT DO NOTHING;
EXCEPTION WHEN check_violation OR not_null_violation OR foreign_key_violation THEN
    NULL;  -- OR IGNORE: drop the violating row(s), like SQLite/MySQL
END $fg_or_ignore$;

DO $fg_or_ignore$
BEGIN
INSERT INTO document_type_descriptions (document_type_id, locale, description)
SELECT id, 'ko', '불명확한 사항에 대한 질문 문서. A로 응답받는다.'
FROM   document_types WHERE series = 'general' AND type_code = 'Q' AND project_id IS NULL ON CONFLICT DO NOTHING;
EXCEPTION WHEN check_violation OR not_null_violation OR foreign_key_violation THEN
    NULL;  -- OR IGNORE: drop the violating row(s), like SQLite/MySQL
END $fg_or_ignore$;
DO $fg_or_ignore$
BEGIN
INSERT INTO document_type_descriptions (document_type_id, locale, description)
SELECT id, 'ja', '不明点についての質問文書。Aで回答を受ける。'
FROM   document_types WHERE series = 'general' AND type_code = 'Q' AND project_id IS NULL ON CONFLICT DO NOTHING;
EXCEPTION WHEN check_violation OR not_null_violation OR foreign_key_violation THEN
    NULL;  -- OR IGNORE: drop the violating row(s), like SQLite/MySQL
END $fg_or_ignore$;
DO $fg_or_ignore$
BEGIN
INSERT INTO document_type_descriptions (document_type_id, locale, description)
SELECT id, 'en', 'Question document for unclear points. Answered via an A document.'
FROM   document_types WHERE series = 'general' AND type_code = 'Q' AND project_id IS NULL ON CONFLICT DO NOTHING;
EXCEPTION WHEN check_violation OR not_null_violation OR foreign_key_violation THEN
    NULL;  -- OR IGNORE: drop the violating row(s), like SQLite/MySQL
END $fg_or_ignore$;

DO $fg_or_ignore$
BEGIN
INSERT INTO document_type_descriptions (document_type_id, locale, description)
SELECT id, 'ko', 'Q에 대한 답변 문서. Q와 1:1 매핑된다.'
FROM   document_types WHERE series = 'general' AND type_code = 'A' AND project_id IS NULL ON CONFLICT DO NOTHING;
EXCEPTION WHEN check_violation OR not_null_violation OR foreign_key_violation THEN
    NULL;  -- OR IGNORE: drop the violating row(s), like SQLite/MySQL
END $fg_or_ignore$;
DO $fg_or_ignore$
BEGIN
INSERT INTO document_type_descriptions (document_type_id, locale, description)
SELECT id, 'ja', 'Qに対する回答文書。Qと1対1で対応する。'
FROM   document_types WHERE series = 'general' AND type_code = 'A' AND project_id IS NULL ON CONFLICT DO NOTHING;
EXCEPTION WHEN check_violation OR not_null_violation OR foreign_key_violation THEN
    NULL;  -- OR IGNORE: drop the violating row(s), like SQLite/MySQL
END $fg_or_ignore$;
DO $fg_or_ignore$
BEGIN
INSERT INTO document_type_descriptions (document_type_id, locale, description)
SELECT id, 'en', 'Answer document for a Q. Maps 1:1 to its Q.'
FROM   document_types WHERE series = 'general' AND type_code = 'A' AND project_id IS NULL ON CONFLICT DO NOTHING;
EXCEPTION WHEN check_violation OR not_null_violation OR foreign_key_violation THEN
    NULL;  -- OR IGNORE: drop the violating row(s), like SQLite/MySQL
END $fg_or_ignore$;

DO $fg_or_ignore$
BEGIN
INSERT INTO document_type_descriptions (document_type_id, locale, description)
SELECT id, 'ko', '운영·세션·조사 진행 기록 문서.'
FROM   document_types WHERE series = 'general' AND type_code = 'L' AND project_id IS NULL ON CONFLICT DO NOTHING;
EXCEPTION WHEN check_violation OR not_null_violation OR foreign_key_violation THEN
    NULL;  -- OR IGNORE: drop the violating row(s), like SQLite/MySQL
END $fg_or_ignore$;
DO $fg_or_ignore$
BEGIN
INSERT INTO document_type_descriptions (document_type_id, locale, description)
SELECT id, 'ja', '運用・セッション・調査の進行記録文書。'
FROM   document_types WHERE series = 'general' AND type_code = 'L' AND project_id IS NULL ON CONFLICT DO NOTHING;
EXCEPTION WHEN check_violation OR not_null_violation OR foreign_key_violation THEN
    NULL;  -- OR IGNORE: drop the violating row(s), like SQLite/MySQL
END $fg_or_ignore$;
DO $fg_or_ignore$
BEGIN
INSERT INTO document_type_descriptions (document_type_id, locale, description)
SELECT id, 'en', 'Log document for operations, sessions, and investigation progress.'
FROM   document_types WHERE series = 'general' AND type_code = 'L' AND project_id IS NULL ON CONFLICT DO NOTHING;
EXCEPTION WHEN check_violation OR not_null_violation OR foreign_key_violation THEN
    NULL;  -- OR IGNORE: drop the violating row(s), like SQLite/MySQL
END $fg_or_ignore$;

DO $fg_or_ignore$
BEGIN
INSERT INTO document_type_descriptions (document_type_id, locale, description)
SELECT id, 'ko', '버그 보고 문서. 재현 절차·기대 동작·실제 동작을 포함한다.'
FROM   document_types WHERE series = 'general' AND type_code = 'B' AND project_id IS NULL ON CONFLICT DO NOTHING;
EXCEPTION WHEN check_violation OR not_null_violation OR foreign_key_violation THEN
    NULL;  -- OR IGNORE: drop the violating row(s), like SQLite/MySQL
END $fg_or_ignore$;
DO $fg_or_ignore$
BEGIN
INSERT INTO document_type_descriptions (document_type_id, locale, description)
SELECT id, 'ja', 'バグ報告文書。再現手順・期待動作・実際の動作を含む。'
FROM   document_types WHERE series = 'general' AND type_code = 'B' AND project_id IS NULL ON CONFLICT DO NOTHING;
EXCEPTION WHEN check_violation OR not_null_violation OR foreign_key_violation THEN
    NULL;  -- OR IGNORE: drop the violating row(s), like SQLite/MySQL
END $fg_or_ignore$;
DO $fg_or_ignore$
BEGIN
INSERT INTO document_type_descriptions (document_type_id, locale, description)
SELECT id, 'en', 'Bug report document. Includes reproduction steps, expected behavior, and actual behavior.'
FROM   document_types WHERE series = 'general' AND type_code = 'B' AND project_id IS NULL ON CONFLICT DO NOTHING;
EXCEPTION WHEN check_violation OR not_null_violation OR foreign_key_violation THEN
    NULL;  -- OR IGNORE: drop the violating row(s), like SQLite/MySQL
END $fg_or_ignore$;

-- CH (Conversation) — never had a document_types.description value (added by 047 after 022)
DO $fg_or_ignore$
BEGIN
INSERT INTO document_type_descriptions (document_type_id, locale, description)
SELECT id, 'ko', '실시간 대화·멘션 기반 상호작용 기록 문서.'
FROM   document_types WHERE series = 'general' AND type_code = 'CH' AND project_id IS NULL ON CONFLICT DO NOTHING;
EXCEPTION WHEN check_violation OR not_null_violation OR foreign_key_violation THEN
    NULL;  -- OR IGNORE: drop the violating row(s), like SQLite/MySQL
END $fg_or_ignore$;
DO $fg_or_ignore$
BEGIN
INSERT INTO document_type_descriptions (document_type_id, locale, description)
SELECT id, 'ja', 'リアルタイム会話・メンションベースのやり取りを記録する文書。'
FROM   document_types WHERE series = 'general' AND type_code = 'CH' AND project_id IS NULL ON CONFLICT DO NOTHING;
EXCEPTION WHEN check_violation OR not_null_violation OR foreign_key_violation THEN
    NULL;  -- OR IGNORE: drop the violating row(s), like SQLite/MySQL
END $fg_or_ignore$;
DO $fg_or_ignore$
BEGIN
INSERT INTO document_type_descriptions (document_type_id, locale, description)
SELECT id, 'en', 'Document recording real-time conversation / mention-based interactions.'
FROM   document_types WHERE series = 'general' AND type_code = 'CH' AND project_id IS NULL ON CONFLICT DO NOTHING;
EXCEPTION WHEN check_violation OR not_null_violation OR foreign_key_violation THEN
    NULL;  -- OR IGNORE: drop the violating row(s), like SQLite/MySQL
END $fg_or_ignore$;

-- ── 3. instruction series (DS, N, T, TS) ─────────────────────────────────────
DO $fg_or_ignore$
BEGIN
INSERT INTO document_type_descriptions (document_type_id, locale, description)
SELECT id, 'ja', 'D(基本設計)の作成を指示する文書。'
FROM   document_types WHERE series = 'instruction' AND type_code = 'DS' AND project_id IS NULL ON CONFLICT DO NOTHING;
EXCEPTION WHEN check_violation OR not_null_violation OR foreign_key_violation THEN
    NULL;  -- OR IGNORE: drop the violating row(s), like SQLite/MySQL
END $fg_or_ignore$;
DO $fg_or_ignore$
BEGIN
INSERT INTO document_type_descriptions (document_type_id, locale, description)
SELECT id, 'en', 'Instructs the writing of a D (Basic Design) document.'
FROM   document_types WHERE series = 'instruction' AND type_code = 'DS' AND project_id IS NULL ON CONFLICT DO NOTHING;
EXCEPTION WHEN check_violation OR not_null_violation OR foreign_key_violation THEN
    NULL;  -- OR IGNORE: drop the violating row(s), like SQLite/MySQL
END $fg_or_ignore$;

DO $fg_or_ignore$
BEGIN
INSERT INTO document_type_descriptions (document_type_id, locale, description)
SELECT id, 'ja', '調査を指示する文書。結果はNR(調査レポート)で受け取る。'
FROM   document_types WHERE series = 'instruction' AND type_code = 'N' AND project_id IS NULL ON CONFLICT DO NOTHING;
EXCEPTION WHEN check_violation OR not_null_violation OR foreign_key_violation THEN
    NULL;  -- OR IGNORE: drop the violating row(s), like SQLite/MySQL
END $fg_or_ignore$;
DO $fg_or_ignore$
BEGIN
INSERT INTO document_type_descriptions (document_type_id, locale, description)
SELECT id, 'en', 'Instructs an investigation. Results come back as an NR (Investigation Report).'
FROM   document_types WHERE series = 'instruction' AND type_code = 'N' AND project_id IS NULL ON CONFLICT DO NOTHING;
EXCEPTION WHEN check_violation OR not_null_violation OR foreign_key_violation THEN
    NULL;  -- OR IGNORE: drop the violating row(s), like SQLite/MySQL
END $fg_or_ignore$;

DO $fg_or_ignore$
BEGIN
INSERT INTO document_type_descriptions (document_type_id, locale, description)
SELECT id, 'ja', '実装作業を指示する文書。結果はTR(作業レポート)で受け取る。'
FROM   document_types WHERE series = 'instruction' AND type_code = 'T' AND project_id IS NULL ON CONFLICT DO NOTHING;
EXCEPTION WHEN check_violation OR not_null_violation OR foreign_key_violation THEN
    NULL;  -- OR IGNORE: drop the violating row(s), like SQLite/MySQL
END $fg_or_ignore$;
DO $fg_or_ignore$
BEGIN
INSERT INTO document_type_descriptions (document_type_id, locale, description)
SELECT id, 'en', 'Instructs an implementation task. Results come back as a TR (Task Report).'
FROM   document_types WHERE series = 'instruction' AND type_code = 'T' AND project_id IS NULL ON CONFLICT DO NOTHING;
EXCEPTION WHEN check_violation OR not_null_violation OR foreign_key_violation THEN
    NULL;  -- OR IGNORE: drop the violating row(s), like SQLite/MySQL
END $fg_or_ignore$;

DO $fg_or_ignore$
BEGIN
INSERT INTO document_type_descriptions (document_type_id, locale, description)
SELECT id, 'ja', 'テストシナリオの作成を指示する文書。結果はTSRで受け取る。'
FROM   document_types WHERE series = 'instruction' AND type_code = 'TS' AND project_id IS NULL ON CONFLICT DO NOTHING;
EXCEPTION WHEN check_violation OR not_null_violation OR foreign_key_violation THEN
    NULL;  -- OR IGNORE: drop the violating row(s), like SQLite/MySQL
END $fg_or_ignore$;
DO $fg_or_ignore$
BEGIN
INSERT INTO document_type_descriptions (document_type_id, locale, description)
SELECT id, 'en', 'Instructs the writing of test scenarios. Results come back as a TSR.'
FROM   document_types WHERE series = 'instruction' AND type_code = 'TS' AND project_id IS NULL ON CONFLICT DO NOTHING;
EXCEPTION WHEN check_violation OR not_null_violation OR foreign_key_violation THEN
    NULL;  -- OR IGNORE: drop the violating row(s), like SQLite/MySQL
END $fg_or_ignore$;

-- ── 4. design series (D, P, L, DB) ───────────────────────────────────────────
DO $fg_or_ignore$
BEGIN
INSERT INTO document_type_descriptions (document_type_id, locale, description)
SELECT id, 'ja', 'コンポーネントの役割・判断フロー・モジュール関係・入出力項目・画面構成を定義。PMの可読性向け。SQL・疑似コードは含まない(→L・P・DBに委譲)。'
FROM   document_types WHERE series = 'design' AND type_code = 'D' AND project_id IS NULL ON CONFLICT DO NOTHING;
EXCEPTION WHEN check_violation OR not_null_violation OR foreign_key_violation THEN
    NULL;  -- OR IGNORE: drop the violating row(s), like SQLite/MySQL
END $fg_or_ignore$;
DO $fg_or_ignore$
BEGIN
INSERT INTO document_type_descriptions (document_type_id, locale, description)
SELECT id, 'en', 'Defines component roles, decision flow, module relationships, input/output items, and screen composition. Written for PM readability. No SQL or pseudocode (delegated to L/P/DB).'
FROM   document_types WHERE series = 'design' AND type_code = 'D' AND project_id IS NULL ON CONFLICT DO NOTHING;
EXCEPTION WHEN check_violation OR not_null_violation OR foreign_key_violation THEN
    NULL;  -- OR IGNORE: drop the violating row(s), like SQLite/MySQL
END $fg_or_ignore$;

DO $fg_or_ignore$
BEGIN
INSERT INTO document_type_descriptions (document_type_id, locale, description)
SELECT id, 'ja', 'APIエンドポイント・リクエスト/レスポンスJSON形式・メッセージスキーマ・ヘッダー規約を定義。'
FROM   document_types WHERE series = 'design' AND type_code = 'P' AND project_id IS NULL ON CONFLICT DO NOTHING;
EXCEPTION WHEN check_violation OR not_null_violation OR foreign_key_violation THEN
    NULL;  -- OR IGNORE: drop the violating row(s), like SQLite/MySQL
END $fg_or_ignore$;
DO $fg_or_ignore$
BEGIN
INSERT INTO document_type_descriptions (document_type_id, locale, description)
SELECT id, 'en', 'Defines API endpoints, request/response JSON formats, message schemas, and header conventions.'
FROM   document_types WHERE series = 'design' AND type_code = 'P' AND project_id IS NULL ON CONFLICT DO NOTHING;
EXCEPTION WHEN check_violation OR not_null_violation OR foreign_key_violation THEN
    NULL;  -- OR IGNORE: drop the violating row(s), like SQLite/MySQL
END $fg_or_ignore$;

DO $fg_or_ignore$
BEGIN
INSERT INTO document_type_descriptions (document_type_id, locale, description)
SELECT id, 'ja', 'アルゴリズム・疑似コード・数式・閾値・状態遷移表・決定木・境界条件を定義。ワーカー向け。'
FROM   document_types WHERE series = 'design' AND type_code = 'L' AND project_id IS NULL ON CONFLICT DO NOTHING;
EXCEPTION WHEN check_violation OR not_null_violation OR foreign_key_violation THEN
    NULL;  -- OR IGNORE: drop the violating row(s), like SQLite/MySQL
END $fg_or_ignore$;
DO $fg_or_ignore$
BEGIN
INSERT INTO document_type_descriptions (document_type_id, locale, description)
SELECT id, 'en', 'Defines algorithms, pseudocode, formulas, thresholds, state transition tables, decision trees, and boundary conditions. For workers.'
FROM   document_types WHERE series = 'design' AND type_code = 'L' AND project_id IS NULL ON CONFLICT DO NOTHING;
EXCEPTION WHEN check_violation OR not_null_violation OR foreign_key_violation THEN
    NULL;  -- OR IGNORE: drop the violating row(s), like SQLite/MySQL
END $fg_or_ignore$;

DO $fg_or_ignore$
BEGIN
INSERT INTO document_type_descriptions (document_type_id, locale, description)
SELECT id, 'ja', 'CREATE TABLE・カラム・インデックス・外部キー・マイグレーション手順を定義。'
FROM   document_types WHERE series = 'design' AND type_code = 'DB' AND project_id IS NULL ON CONFLICT DO NOTHING;
EXCEPTION WHEN check_violation OR not_null_violation OR foreign_key_violation THEN
    NULL;  -- OR IGNORE: drop the violating row(s), like SQLite/MySQL
END $fg_or_ignore$;
DO $fg_or_ignore$
BEGIN
INSERT INTO document_type_descriptions (document_type_id, locale, description)
SELECT id, 'en', 'Defines CREATE TABLE statements, columns, indexes, foreign keys, and migration procedures.'
FROM   document_types WHERE series = 'design' AND type_code = 'DB' AND project_id IS NULL ON CONFLICT DO NOTHING;
EXCEPTION WHEN check_violation OR not_null_violation OR foreign_key_violation THEN
    NULL;  -- OR IGNORE: drop the violating row(s), like SQLite/MySQL
END $fg_or_ignore$;

-- ── 5. work series (NR, TR, TSR, V, C) ───────────────────────────────────────
DO $fg_or_ignore$
BEGIN
INSERT INTO document_type_descriptions (document_type_id, locale, description)
SELECT id, 'ja', 'N(調査指示)の結果報告書。'
FROM   document_types WHERE series = 'work' AND type_code = 'NR' AND project_id IS NULL ON CONFLICT DO NOTHING;
EXCEPTION WHEN check_violation OR not_null_violation OR foreign_key_violation THEN
    NULL;  -- OR IGNORE: drop the violating row(s), like SQLite/MySQL
END $fg_or_ignore$;
DO $fg_or_ignore$
BEGIN
INSERT INTO document_type_descriptions (document_type_id, locale, description)
SELECT id, 'en', 'Result report for an N (Investigation Instruction).'
FROM   document_types WHERE series = 'work' AND type_code = 'NR' AND project_id IS NULL ON CONFLICT DO NOTHING;
EXCEPTION WHEN check_violation OR not_null_violation OR foreign_key_violation THEN
    NULL;  -- OR IGNORE: drop the violating row(s), like SQLite/MySQL
END $fg_or_ignore$;

DO $fg_or_ignore$
BEGIN
INSERT INTO document_type_descriptions (document_type_id, locale, description)
SELECT id, 'ja', 'T(作業指示)の結果報告書。'
FROM   document_types WHERE series = 'work' AND type_code = 'TR' AND project_id IS NULL ON CONFLICT DO NOTHING;
EXCEPTION WHEN check_violation OR not_null_violation OR foreign_key_violation THEN
    NULL;  -- OR IGNORE: drop the violating row(s), like SQLite/MySQL
END $fg_or_ignore$;
DO $fg_or_ignore$
BEGIN
INSERT INTO document_type_descriptions (document_type_id, locale, description)
SELECT id, 'en', 'Result report for a T (Task Instruction).'
FROM   document_types WHERE series = 'work' AND type_code = 'TR' AND project_id IS NULL ON CONFLICT DO NOTHING;
EXCEPTION WHEN check_violation OR not_null_violation OR foreign_key_violation THEN
    NULL;  -- OR IGNORE: drop the violating row(s), like SQLite/MySQL
END $fg_or_ignore$;

DO $fg_or_ignore$
BEGIN
INSERT INTO document_type_descriptions (document_type_id, locale, description)
SELECT id, 'ja', 'TS(テストシナリオ指示)の結果報告書。'
FROM   document_types WHERE series = 'work' AND type_code = 'TSR' AND project_id IS NULL ON CONFLICT DO NOTHING;
EXCEPTION WHEN check_violation OR not_null_violation OR foreign_key_violation THEN
    NULL;  -- OR IGNORE: drop the violating row(s), like SQLite/MySQL
END $fg_or_ignore$;
DO $fg_or_ignore$
BEGIN
INSERT INTO document_type_descriptions (document_type_id, locale, description)
SELECT id, 'en', 'Result report for a TS (Test Scenario Instruction).'
FROM   document_types WHERE series = 'work' AND type_code = 'TSR' AND project_id IS NULL ON CONFLICT DO NOTHING;
EXCEPTION WHEN check_violation OR not_null_violation OR foreign_key_violation THEN
    NULL;  -- OR IGNORE: drop the violating row(s), like SQLite/MySQL
END $fg_or_ignore$;

DO $fg_or_ignore$
BEGIN
INSERT INTO document_type_descriptions (document_type_id, locale, description)
SELECT id, 'ja', '特定の成果物に対する検収・レビューを依頼する文書。結果はVRで受け取る。'
FROM   document_types WHERE series = 'work' AND type_code = 'V' AND project_id IS NULL ON CONFLICT DO NOTHING;
EXCEPTION WHEN check_violation OR not_null_violation OR foreign_key_violation THEN
    NULL;  -- OR IGNORE: drop the violating row(s), like SQLite/MySQL
END $fg_or_ignore$;
DO $fg_or_ignore$
BEGIN
INSERT INTO document_type_descriptions (document_type_id, locale, description)
SELECT id, 'en', 'Requests inspection/review of a specific deliverable. Results come back as a VR.'
FROM   document_types WHERE series = 'work' AND type_code = 'V' AND project_id IS NULL ON CONFLICT DO NOTHING;
EXCEPTION WHEN check_violation OR not_null_violation OR foreign_key_violation THEN
    NULL;  -- OR IGNORE: drop the violating row(s), like SQLite/MySQL
END $fg_or_ignore$;

DO $fg_or_ignore$
BEGIN
INSERT INTO document_type_descriptions (document_type_id, locale, description)
SELECT id, 'ja', 'コミット作業の指示または記録。'
FROM   document_types WHERE series = 'work' AND type_code = 'C' AND project_id IS NULL ON CONFLICT DO NOTHING;
EXCEPTION WHEN check_violation OR not_null_violation OR foreign_key_violation THEN
    NULL;  -- OR IGNORE: drop the violating row(s), like SQLite/MySQL
END $fg_or_ignore$;
DO $fg_or_ignore$
BEGIN
INSERT INTO document_type_descriptions (document_type_id, locale, description)
SELECT id, 'en', 'Commit work instruction or record.'
FROM   document_types WHERE series = 'work' AND type_code = 'C' AND project_id IS NULL ON CONFLICT DO NOTHING;
EXCEPTION WHEN check_violation OR not_null_violation OR foreign_key_violation THEN
    NULL;  -- OR IGNORE: drop the violating row(s), like SQLite/MySQL
END $fg_or_ignore$;

-- ── 6. action series (AC, RJ) ────────────────────────────────────────────────
DO $fg_or_ignore$
BEGIN
INSERT INTO document_type_descriptions (document_type_id, locale, description)
SELECT id, 'ja', '成果物承認アクションの記録。'
FROM   document_types WHERE series = 'action' AND type_code = 'AC' AND project_id IS NULL ON CONFLICT DO NOTHING;
EXCEPTION WHEN check_violation OR not_null_violation OR foreign_key_violation THEN
    NULL;  -- OR IGNORE: drop the violating row(s), like SQLite/MySQL
END $fg_or_ignore$;
DO $fg_or_ignore$
BEGIN
INSERT INTO document_type_descriptions (document_type_id, locale, description)
SELECT id, 'en', 'Record of a deliverable approval action.'
FROM   document_types WHERE series = 'action' AND type_code = 'AC' AND project_id IS NULL ON CONFLICT DO NOTHING;
EXCEPTION WHEN check_violation OR not_null_violation OR foreign_key_violation THEN
    NULL;  -- OR IGNORE: drop the violating row(s), like SQLite/MySQL
END $fg_or_ignore$;

DO $fg_or_ignore$
BEGIN
INSERT INTO document_type_descriptions (document_type_id, locale, description)
SELECT id, 'ja', '成果物却下アクションの記録。'
FROM   document_types WHERE series = 'action' AND type_code = 'RJ' AND project_id IS NULL ON CONFLICT DO NOTHING;
EXCEPTION WHEN check_violation OR not_null_violation OR foreign_key_violation THEN
    NULL;  -- OR IGNORE: drop the violating row(s), like SQLite/MySQL
END $fg_or_ignore$;
DO $fg_or_ignore$
BEGIN
INSERT INTO document_type_descriptions (document_type_id, locale, description)
SELECT id, 'en', 'Record of a deliverable rejection action.'
FROM   document_types WHERE series = 'action' AND type_code = 'RJ' AND project_id IS NULL ON CONFLICT DO NOTHING;
EXCEPTION WHEN check_violation OR not_null_violation OR foreign_key_violation THEN
    NULL;  -- OR IGNORE: drop the violating row(s), like SQLite/MySQL
END $fg_or_ignore$;

