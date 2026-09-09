-- 059_ai_provider_settings.sql
-- AI provider settings (flowgate.default.0164: R0001 -> D0002 -> P0003 -> L0004 -> DB0005).
-- Ordered provider list ("routing chain": array order = fallback order) + current default
-- selection, in two scopes. Scope follows the document_types precedent: project_id NULL =
-- global row, value = that project's own list. The project tri-state mode
-- (inherit/disabled/custom) is a new project_settings column where NULL = unset = inherit,
-- so existing rows need no backfill. The global default selection is a system_settings
-- K/V row ('ai_default_provider_id') written on first save — no seed here.
-- Additive only. ai_mode carries NO CHECK in any dialect (kept aligned with SQLite, which
-- cannot ADD COLUMN with a CHECK); the service layer validates values (DB0005 §3).
-- api_key stores the raw secret (same trust boundary as env-vars values); at-rest
-- encryption is DEFERRED (L0004 §2.3).

CREATE TABLE IF NOT EXISTS ai_providers (
    provider_id  TEXT PRIMARY KEY,
    project_id   TEXT REFERENCES projects(project_id) ON DELETE CASCADE,
    name         TEXT    NOT NULL,
    exec_type    TEXT    NOT NULL CHECK (exec_type IN ('cli', 'api')),
    kind         TEXT    NOT NULL,
    enabled      INTEGER NOT NULL DEFAULT 1,
    cli_command  TEXT,
    api_base_url TEXT,
    api_model    TEXT,
    api_key      TEXT,
    sort_order   INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT    NOT NULL,
    updated_at   TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ai_providers_project ON ai_providers(project_id);
-- Display-name uniqueness per scope (ux_doc_types_global/project precedent).
CREATE UNIQUE INDEX IF NOT EXISTS ux_ai_providers_global_name
    ON ai_providers(name)
    WHERE project_id IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS ux_ai_providers_project_name
    ON ai_providers(project_id, name)
    WHERE project_id IS NOT NULL;

ALTER TABLE project_settings ADD COLUMN ai_mode TEXT;
ALTER TABLE project_settings ADD COLUMN ai_default_provider_id TEXT;
