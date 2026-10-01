-- 135_ai_project_split.sql  (mysql)
-- flowgate.v02.0017 CH0006 (4): AI evaluation and AI groups leave the project, and the project AI
-- policy is retired. AI 관리 is one screen that needs no project.
--
--   ai_evaluations / ai_evaluation_sets / ai_groups  project_id is dropped. Names are unique
--                                over the whole list; a name that now collides with an older
--                                row gets ' · <former project id>' appended.
--   ai_groups.default_key        'default' on the one default team, NULL otherwise. When several
--                                projects had a default team, the most recently updated keeps it.
--   project_ai_policies / project_ai_policy_providers  dropped. Every system AI is usable
--                                everywhere; ai_providers.project_access is no longer read.

-- Duplicates first, while project_id still tells the rows apart. The oldest row keeps the name.
UPDATE ai_groups g
JOIN (SELECT a.ai_group_id, CONCAT(a.name_key, ' · ', LOWER(a.project_id)) AS new_key
      FROM ai_groups a
      JOIN ai_groups o ON o.name_key_hash = a.name_key_hash
                      AND (o.created_at < a.created_at
                           OR (o.created_at = a.created_at AND o.ai_group_id < a.ai_group_id))
      GROUP BY a.ai_group_id, a.name_key, a.project_id) dup ON dup.ai_group_id = g.ai_group_id
SET g.name = CONCAT(g.name, ' · ', g.project_id),
    g.name_key = dup.new_key,
    g.name_key_hash = SHA2(dup.new_key, 256);

UPDATE ai_evaluation_sets s
JOIN (SELECT a.set_id, CONCAT(a.name_key, ' · ', LOWER(a.project_id)) AS new_key
      FROM ai_evaluation_sets a
      JOIN ai_evaluation_sets o ON o.name_key_hash = a.name_key_hash
                               AND (o.created_at < a.created_at
                                    OR (o.created_at = a.created_at AND o.set_id < a.set_id))
      GROUP BY a.set_id, a.name_key, a.project_id) dup ON dup.set_id = s.set_id
SET s.name = CONCAT(s.name, ' · ', s.project_id),
    s.name_key = dup.new_key,
    s.name_key_hash = SHA2(dup.new_key, 256);

-- One default team for the whole list.
UPDATE ai_groups SET default_key = NULL
WHERE default_key IS NOT NULL
  AND ai_group_id <> (SELECT keep.ai_group_id FROM (
        SELECT ai_group_id FROM ai_groups WHERE default_key IS NOT NULL
        ORDER BY updated_at DESC, ai_group_id DESC LIMIT 1) AS keep);
UPDATE ai_groups SET default_key = 'default' WHERE default_key IS NOT NULL;

-- ai_groups
ALTER TABLE ai_groups DROP FOREIGN KEY fk_ai_groups_project;
ALTER TABLE ai_groups DROP INDEX uq_ai_groups_name_hash;
ALTER TABLE ai_groups DROP INDEX idx_ai_groups_project_updated;
ALTER TABLE ai_groups DROP COLUMN project_id;
ALTER TABLE ai_groups ADD UNIQUE KEY uq_ai_groups_name_hash (name_key_hash);
CREATE INDEX idx_ai_groups_updated ON ai_groups(updated_at, ai_group_id);

-- ai_evaluation_sets
ALTER TABLE ai_evaluation_sets DROP FOREIGN KEY fk_ai_evaluation_sets_project;
ALTER TABLE ai_evaluation_sets DROP INDEX uq_ai_evaluation_sets_name_hash;
ALTER TABLE ai_evaluation_sets DROP COLUMN project_id;
ALTER TABLE ai_evaluation_sets ADD UNIQUE KEY uq_ai_evaluation_sets_name_hash (name_key_hash);

-- ai_evaluations. The dedupe key was per project; the next start recomputes it per
-- (target, judge). A row still active here was cut off by the deploy restart and
-- recover_orphans() fails it at boot.
UPDATE ai_evaluations SET active_dedupe_key = NULL WHERE active_dedupe_key IS NOT NULL;
ALTER TABLE ai_evaluations DROP FOREIGN KEY fk_ai_evaluations_project;
ALTER TABLE ai_evaluations DROP INDEX idx_ai_evaluations_project_created;
ALTER TABLE ai_evaluations DROP INDEX idx_ai_evaluations_target;
ALTER TABLE ai_evaluations DROP COLUMN project_id;
CREATE INDEX idx_ai_evaluations_created ON ai_evaluations(created_at, evaluation_id);
CREATE INDEX idx_ai_evaluations_target ON ai_evaluations(target_provider_id, created_at);

-- Project AI policy
DROP TABLE IF EXISTS project_ai_policy_providers;
DROP TABLE IF EXISTS project_ai_policies;
