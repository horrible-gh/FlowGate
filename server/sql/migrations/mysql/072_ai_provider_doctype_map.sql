-- 072_ai_provider_doctype_map.sql
-- Per-document-type AI provider assignment for the continuous (unmanned) chain
-- (flowgate.default.0317: R0001 -> NR0003 -> D0004 -> T0005 구현 단계).
--
-- The continuous chain runs one provider start-to-finish today. This table stores the
-- "문서 종류 -> 프로바이더" 배정 규칙 the hop provider decider reads at each step boundary
-- (D0004 §1: 배정은 프로젝트 단위). One row = one (project, doc_type) assignment.
--
-- Scope mirrors ai_providers/document_types: project_id is the owning project. The assigned
-- provider_id references an ai_providers row (which may itself be a global/inherited row).
-- FKs cascade so a discarded project or a provider removed from the routing chain can never
-- leave a dangling assignment; the service resolver additionally ignores any assignment whose
-- provider is not in the project's effective ENABLED chain, so a disabled/foreign provider
-- silently falls back to the default.
--
-- Additive only — an empty table reproduces today's single-provider behavior.

CREATE TABLE IF NOT EXISTS ai_provider_doctype_map (
    id          INTEGER PRIMARY KEY AUTO_INCREMENT,
    project_id  VARCHAR(191) NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
    doc_type    VARCHAR(191) NOT NULL,
    provider_id VARCHAR(191) NOT NULL REFERENCES ai_providers(provider_id) ON DELETE CASCADE,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    UNIQUE (project_id, doc_type)
);

CREATE INDEX IF NOT EXISTS idx_aipdm_project ON ai_provider_doctype_map(project_id);
