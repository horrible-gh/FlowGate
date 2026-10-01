-- 135_ai_project_split.sql  (postgres)
-- flowgate.v02.0017 CH0006 (4): AI evaluation and AI groups leave the project, and the project AI
-- policy is retired. AI 관리 is one screen that needs no project.
--
--   ai_evaluations / ai_evaluation_sets / ai_groups  project_id is dropped (its FK, the per-project
--                                UNIQUE and indexes go with the column). Names are unique over
--                                the whole list; a name that now collides with an older row gets
--                                ' · <former project id>' appended.
--   ai_groups.default_key        'default' on the one default team, NULL otherwise. When several
--                                projects had a default team, the most recently updated keeps it.
--   project_ai_policies / project_ai_policy_providers  dropped. Every system AI is usable
--                                everywhere; ai_providers.project_access is no longer read.

-- Duplicates first, while project_id still tells the rows apart. The oldest row keeps the name.
UPDATE ai_groups g
SET name = g.name || ' · ' || g.project_id,
    name_key = g.name_key || ' · ' || lower(g.project_id),
    name_key_hash = encode(sha256(convert_to(g.name_key || ' · ' || lower(g.project_id), 'UTF8')), 'hex')
WHERE EXISTS (SELECT 1 FROM ai_groups o
              WHERE o.name_key_hash = g.name_key_hash
                AND (o.created_at < g.created_at
                     OR (o.created_at = g.created_at AND o.ai_group_id < g.ai_group_id)));

UPDATE ai_evaluation_sets s
SET name = s.name || ' · ' || s.project_id,
    name_key = s.name_key || ' · ' || lower(s.project_id),
    name_key_hash = encode(sha256(convert_to(s.name_key || ' · ' || lower(s.project_id), 'UTF8')), 'hex')
WHERE EXISTS (SELECT 1 FROM ai_evaluation_sets o
              WHERE o.name_key_hash = s.name_key_hash
                AND (o.created_at < s.created_at
                     OR (o.created_at = s.created_at AND o.set_id < s.set_id)));

-- One default team for the whole list.
UPDATE ai_groups SET default_key = NULL
WHERE default_key IS NOT NULL
  AND ai_group_id <> (SELECT ai_group_id FROM ai_groups WHERE default_key IS NOT NULL
                      ORDER BY updated_at DESC, ai_group_id DESC LIMIT 1);
UPDATE ai_groups SET default_key = 'default' WHERE default_key IS NOT NULL;

-- ai_groups
DROP INDEX IF EXISTS idx_ai_groups_project_updated;
ALTER TABLE ai_groups DROP COLUMN IF EXISTS project_id;
CREATE UNIQUE INDEX IF NOT EXISTS uq_ai_groups_name_hash ON ai_groups(name_key_hash);
CREATE INDEX IF NOT EXISTS idx_ai_groups_updated ON ai_groups(updated_at, ai_group_id);

-- ai_evaluation_sets
ALTER TABLE ai_evaluation_sets DROP COLUMN IF EXISTS project_id;
CREATE UNIQUE INDEX IF NOT EXISTS uq_ai_evaluation_sets_name_hash ON ai_evaluation_sets(name_key_hash);

-- ai_evaluations. The dedupe key was per project; the next start recomputes it per
-- (target, judge). A row still active here was cut off by the deploy restart and
-- recover_orphans() fails it at boot.
UPDATE ai_evaluations SET active_dedupe_key = NULL WHERE active_dedupe_key IS NOT NULL;
DROP INDEX IF EXISTS idx_ai_evaluations_project_created;
DROP INDEX IF EXISTS idx_ai_evaluations_target;
ALTER TABLE ai_evaluations DROP COLUMN IF EXISTS project_id;
CREATE INDEX IF NOT EXISTS idx_ai_evaluations_created ON ai_evaluations(created_at, evaluation_id);
CREATE INDEX IF NOT EXISTS idx_ai_evaluations_target ON ai_evaluations(target_provider_id, created_at);

-- Project AI policy
DROP TABLE IF EXISTS project_ai_policy_providers;
DROP TABLE IF EXISTS project_ai_policies;
