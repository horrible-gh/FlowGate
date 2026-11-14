-- 189_repair_pg_conversation_work_plan_doctypes.sql
-- flowgate.v02gui.0017 (NR 0003 A, option (ra)): PostgreSQL only, hand-authored.
--
-- Why: on PostgreSQL the 023/025 rebuild left the document_types id sequence behind MAX(id)
-- (see 046a_pg_document_types_sequence_realign.sql). A database that went through 047 and 078b
-- in that state has NO CH (conversation) and NO WP (work plan) rows: their id-less INSERTs drew
-- an id that was already taken and ON CONFLICT DO NOTHING dropped them without an error.
-- Databases that worked around the 113 failure with a manual setval are in the same state.
--
-- What: re-seed the global CH and WP system types (rows, ko/ja/en names, ko/ja/en descriptions)
-- with exactly the values of 047, 078b and 074c - but only where they are missing.
--   * document_types rows: WHERE NOT EXISTS on (project_id IS NULL, type_code), so a database
--     that already has them is left alone (fresh databases after 046a, SQLite/MySQL-like data).
--   * names / descriptions: ON CONFLICT DO NOTHING on the (document_type_id, locale) key, so
--     existing (possibly edited) labels are never overwritten.
-- The sequence realign from 046a is repeated first so the inserts below can never collide,
-- even on a database whose sequence was moved by hand after 046a ran. Re-running is a no-op.
--
-- No SQLite / MySQL counterpart: those dialects never lost the rows. Listed in
-- _PG_HAND_AUTHORED_FILES of server/tools/regen_dialect_migrations.py.
DO $fg_seq_realign$
DECLARE
    seq_name    text;
    max_id      bigint;
    seq_last    bigint;
    seq_called  boolean;
    next_now    bigint;
    next_target bigint;
BEGIN
    IF to_regclass('document_types') IS NULL THEN
        RETURN;
    END IF;
    seq_name := pg_get_serial_sequence('document_types', 'id');
    IF seq_name IS NULL THEN
        RETURN;
    END IF;
    SELECT MAX(id) INTO max_id FROM document_types;
    IF max_id IS NULL THEN
        RETURN;
    END IF;
    EXECUTE format('SELECT last_value, is_called FROM %s', seq_name) INTO seq_last, seq_called;
    IF seq_called THEN
        next_now := seq_last + 1;
    ELSE
        next_now := seq_last;
    END IF;
    next_target := GREATEST(next_now, max_id + 1);
    IF next_target > next_now THEN
        PERFORM setval(seq_name, next_target, false);
    END IF;
END $fg_seq_realign$;

-- CH (conversation): 047 row + names, 074c descriptions.
INSERT INTO document_types (project_id, type_code, type_name, series, is_system, is_active, sort_order, created_at, updated_at)
SELECT NULL, 'CH', '대화', 'general', 1, 1, 25, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
WHERE NOT EXISTS (SELECT 1 FROM document_types WHERE project_id IS NULL AND type_code = 'CH');
INSERT INTO document_type_names (document_type_id, locale, type_name)
SELECT id, 'ko', '대화' FROM document_types WHERE series = 'general' AND type_code = 'CH' AND project_id IS NULL ON CONFLICT DO NOTHING;
INSERT INTO document_type_names (document_type_id, locale, type_name)
SELECT id, 'ja', '会話' FROM document_types WHERE series = 'general' AND type_code = 'CH' AND project_id IS NULL ON CONFLICT DO NOTHING;
INSERT INTO document_type_names (document_type_id, locale, type_name)
SELECT id, 'en', 'Conversation' FROM document_types WHERE series = 'general' AND type_code = 'CH' AND project_id IS NULL ON CONFLICT DO NOTHING;
INSERT INTO document_type_descriptions (document_type_id, locale, description)
SELECT id, 'ko', '실시간 대화·멘션 기반 상호작용 기록 문서.' FROM document_types WHERE series = 'general' AND type_code = 'CH' AND project_id IS NULL ON CONFLICT DO NOTHING;
INSERT INTO document_type_descriptions (document_type_id, locale, description)
SELECT id, 'ja', 'リアルタイム会話・メンションベースのやり取りを記録する文書。' FROM document_types WHERE series = 'general' AND type_code = 'CH' AND project_id IS NULL ON CONFLICT DO NOTHING;
INSERT INTO document_type_descriptions (document_type_id, locale, description)
SELECT id, 'en', 'Document recording real-time conversation / mention-based interactions.' FROM document_types WHERE series = 'general' AND type_code = 'CH' AND project_id IS NULL ON CONFLICT DO NOTHING;

-- WP (work plan): 078b row + names + descriptions.
INSERT INTO document_types (project_id, type_code, type_name, series, is_system, is_active, sort_order, created_at, updated_at)
SELECT NULL, 'WP', '작업계획', 'general', 1, 1, 30, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
WHERE NOT EXISTS (SELECT 1 FROM document_types WHERE project_id IS NULL AND type_code = 'WP');
INSERT INTO document_type_names (document_type_id, locale, type_name)
SELECT id, 'ko', '작업계획' FROM document_types WHERE series = 'general' AND type_code = 'WP' AND project_id IS NULL ON CONFLICT DO NOTHING;
INSERT INTO document_type_names (document_type_id, locale, type_name)
SELECT id, 'ja', '作業計画' FROM document_types WHERE series = 'general' AND type_code = 'WP' AND project_id IS NULL ON CONFLICT DO NOTHING;
INSERT INTO document_type_names (document_type_id, locale, type_name)
SELECT id, 'en', 'Work Plan' FROM document_types WHERE series = 'general' AND type_code = 'WP' AND project_id IS NULL ON CONFLICT DO NOTHING;
INSERT INTO document_type_descriptions (document_type_id, locale, description)
SELECT id, 'ko', '설계 장수와 작업 세트수, 단계별 공급자를 미리 정해 두는 계획 문서. 실행을 강제하지 않는 자문형(advisory) 문서다.' FROM document_types WHERE series = 'general' AND type_code = 'WP' AND project_id IS NULL ON CONFLICT DO NOTHING;
INSERT INTO document_type_descriptions (document_type_id, locale, description)
SELECT id, 'ja', '設計の枚数と作業セット数、段階ごとの提供者をあらかじめ決めておく計画文書。実行を強制しない助言型(advisory)の文書。' FROM document_types WHERE series = 'general' AND type_code = 'WP' AND project_id IS NULL ON CONFLICT DO NOTHING;
INSERT INTO document_type_descriptions (document_type_id, locale, description)
SELECT id, 'en', 'Plan document fixing design sheet counts, work set counts, and the provider for each step. Advisory only — it never forces execution.' FROM document_types WHERE series = 'general' AND type_code = 'WP' AND project_id IS NULL ON CONFLICT DO NOTHING;
