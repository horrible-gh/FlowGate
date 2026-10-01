-- 133_ai_provider_exec_location.sql  (postgres)
-- flowgate.v02.0017 CH0006 (2): where an AI runs, the other half of the 2x2 execution mode
-- shown on the AI 관리 screen (local/remote x CLI/API; exec_type stays the CLI/API half).
--   ai_providers.exec_location  'local'  = FlowGate (or its embedded Agent) runs the AI,
--                               'remote' = a receiving Agent on another machine runs it.
-- Every existing row ran locally, so the default keeps its meaning.

ALTER TABLE ai_providers ADD COLUMN IF NOT EXISTS exec_location TEXT NOT NULL DEFAULT 'local' CHECK (exec_location IN ('local', 'remote'));
