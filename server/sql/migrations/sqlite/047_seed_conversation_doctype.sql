-- 047_seed_conversation_doctype.sql
-- T0044.0009 / L0044.0008 §2, §10: register the conversation (chat) document type.
--
-- Decision (R0044.0001 answer): the conversation type code is 'CH' (CHat). 'C' was
-- rejected because it is already an active system type (커밋/Commit, series 'work'),
-- and the system identifies types by the bare letter — a second 'C' would be
-- indistinguishable at that layer. 'CH' is a 2-letter code consistent with the
-- existing convention (DS/NR/TR/DB/AC/RJ/TS/TSR) and collides with nothing.
--
-- This is a DATA insert (a global system type row), not a schema change
-- (NR0044.0003 / L0044.0008 §11: DB design is 0 pages). Idempotent via
-- INSERT OR IGNORE, so rerunning is safe.

BEGIN;

-- Global system type row (project_id=NULL, is_system=1). general series, like M (memo).
-- sort_order 25 places it right after M (20) within the general series.
INSERT OR IGNORE INTO document_types
    (project_id, type_code, type_name, series, is_system, is_active, sort_order, created_at, updated_at)
VALUES
    (NULL, 'CH', '대화', 'general', 1, 1, 25, datetime('now'), datetime('now'));

-- Localized display names (document_type_names: ko / ja / en), mirroring the M seed.
INSERT OR IGNORE INTO document_type_names (document_type_id, locale, type_name)
SELECT id, 'ko', '대화'
FROM   document_types
WHERE  series = 'general' AND type_code = 'CH' AND project_id IS NULL;

INSERT OR IGNORE INTO document_type_names (document_type_id, locale, type_name)
SELECT id, 'ja', '会話'
FROM   document_types
WHERE  series = 'general' AND type_code = 'CH' AND project_id IS NULL;

INSERT OR IGNORE INTO document_type_names (document_type_id, locale, type_name)
SELECT id, 'en', 'Conversation'
FROM   document_types
WHERE  series = 'general' AND type_code = 'CH' AND project_id IS NULL;

COMMIT;
