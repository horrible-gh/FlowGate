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
-- MySQL has no partial unique index: per-scope display-name uniqueness is enforced by the
-- service layer only (duplicate_name), the same trade-off document_types accepts.
-- api_key stores the raw secret (same trust boundary as env-vars values); at-rest
-- encryption is DEFERRED (L0004 §2.3).

CREATE TABLE IF NOT EXISTS ai_providers (
    provider_id  VARCHAR(191) NOT NULL PRIMARY KEY,
    project_id   VARCHAR(191) NULL,
    name         TEXT    NOT NULL,
    exec_type    VARCHAR(10) NOT NULL CHECK (exec_type IN ('cli', 'api')),
    kind         VARCHAR(50) NOT NULL,
    enabled      INTEGER NOT NULL DEFAULT 1,
    cli_command  TEXT    NULL,
    api_base_url TEXT    NULL,
    api_model    TEXT    NULL,
    api_key      TEXT    NULL,
    sort_order   INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT    NOT NULL,
    updated_at   TEXT    NOT NULL,
    CONSTRAINT fk_aip_project
        FOREIGN KEY (project_id) REFERENCES projects(project_id) ON DELETE CASCADE
);

CREATE INDEX idx_ai_providers_project ON ai_providers(project_id);

ALTER TABLE project_settings ADD COLUMN ai_mode TEXT NULL;
ALTER TABLE project_settings ADD COLUMN ai_default_provider_id TEXT NULL;
