-- 131_ai_owner_scope_project_policy.sql
-- flowgate.v02.0017 TR0003 rev7 (Q 361~363). Same meaning as sqlite/131:
-- ai_providers.owner_scope/owner_user_id/project_access, project_ai_policies,
-- project_ai_policy_providers; project-owned rows become 'listed' system AI allowed only by the
-- project that owned them (provider_id unchanged).
-- MySQL has no partial unique index and ai_providers.name is TEXT: name uniqueness per owner
-- stays a service-layer check (duplicate_name), as in 059.

ALTER TABLE ai_providers ADD COLUMN owner_scope VARCHAR(10) NOT NULL DEFAULT 'system' CHECK (owner_scope IN ('system', 'user'));
ALTER TABLE ai_providers ADD COLUMN owner_user_id VARCHAR(191) NULL;
ALTER TABLE ai_providers ADD CONSTRAINT fk_aip_owner_user
    FOREIGN KEY (owner_user_id) REFERENCES users(user_id) ON DELETE CASCADE;
ALTER TABLE ai_providers ADD COLUMN project_access VARCHAR(10) NOT NULL DEFAULT 'all' CHECK (project_access IN ('all', 'listed'));
CREATE INDEX idx_ai_providers_owner ON ai_providers(owner_scope, owner_user_id);

CREATE TABLE project_ai_policies (
    project_id VARCHAR(191) NOT NULL PRIMARY KEY,
    ai_enabled TINYINT NOT NULL DEFAULT 1 CHECK (ai_enabled IN (0, 1)),
    system_ai_scope VARCHAR(10) NOT NULL DEFAULT 'all' CHECK (system_ai_scope IN ('all', 'selected')),
    allow_user_ai TINYINT NOT NULL DEFAULT 0 CHECK (allow_user_ai IN (0, 1)),
    default_provider_id VARCHAR(191) NULL,
    updated_by VARCHAR(191) NULL,
    created_at VARCHAR(64) NOT NULL,
    updated_at VARCHAR(64) NOT NULL,
    CONSTRAINT fk_pap_project FOREIGN KEY (project_id) REFERENCES projects(project_id) ON DELETE CASCADE,
    CONSTRAINT fk_pap_default FOREIGN KEY (default_provider_id) REFERENCES ai_providers(provider_id) ON DELETE SET NULL,
    CONSTRAINT fk_pap_updated_by FOREIGN KEY (updated_by) REFERENCES users(user_id) ON DELETE SET NULL
);

CREATE TABLE project_ai_policy_providers (
    project_id VARCHAR(191) NOT NULL,
    provider_id VARCHAR(191) NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at VARCHAR(64) NOT NULL,
    PRIMARY KEY (project_id, provider_id),
    KEY idx_project_ai_policy_providers_provider (provider_id),
    CONSTRAINT fk_papp_project FOREIGN KEY (project_id) REFERENCES projects(project_id) ON DELETE CASCADE,
    CONSTRAINT fk_papp_provider FOREIGN KEY (provider_id) REFERENCES ai_providers(provider_id) ON DELETE CASCADE
);

INSERT INTO project_ai_policies (project_id, ai_enabled, system_ai_scope, allow_user_ai,
                                 default_provider_id, created_at, updated_at)
SELECT p.project_id,
       CASE WHEN ps.ai_mode = 'disabled' THEN 0 ELSE 1 END,
       CASE WHEN EXISTS (SELECT 1 FROM ai_providers o WHERE o.project_id = p.project_id)
                 AND COALESCE(ps.ai_mode, 'inherit') IN ('custom', 'disabled')
            THEN 'selected' ELSE 'all' END,
       0,
       CASE WHEN COALESCE(ps.ai_mode, 'inherit') IN ('custom', 'disabled')
                 AND EXISTS (SELECT 1 FROM ai_providers d
                             WHERE d.provider_id = ps.ai_default_provider_id
                               AND d.project_id = p.project_id)
            THEN ps.ai_default_provider_id END,
       COALESCE(ps.updated_at, (SELECT MAX(o.updated_at) FROM ai_providers o WHERE o.project_id = p.project_id)),
       COALESCE(ps.updated_at, (SELECT MAX(o.updated_at) FROM ai_providers o WHERE o.project_id = p.project_id))
FROM projects p
LEFT JOIN project_settings ps ON ps.project_id = p.project_id
WHERE ps.ai_mode IS NOT NULL
   OR EXISTS (SELECT 1 FROM ai_providers o WHERE o.project_id = p.project_id);

INSERT INTO project_ai_policy_providers (project_id, provider_id, sort_order, created_at)
SELECT project_id, provider_id, sort_order, updated_at FROM ai_providers WHERE project_id IS NOT NULL;

UPDATE ai_providers a
JOIN (SELECT LOWER(name) AS name_key FROM ai_providers GROUP BY LOWER(name) HAVING COUNT(*) > 1) dup
  ON LOWER(a.name) = dup.name_key
SET a.name = CONCAT(a.name, ' · ', a.project_id)
WHERE a.project_id IS NOT NULL;

-- Promoted rows follow the existing system AI in the system list (the next save renumbers).
UPDATE ai_providers SET project_access = 'listed', sort_order = sort_order + 1000, project_id = NULL
WHERE project_id IS NOT NULL;
