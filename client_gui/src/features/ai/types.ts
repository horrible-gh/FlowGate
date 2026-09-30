// AI Settings types (v0.2 flowgate.v02.0015 T0003)

export type ProviderExecType = 'cli' | 'api'

export interface ProviderItem {
  id: string
  name: string
  exec_type: ProviderExecType
  kind: string
  enabled: boolean
  cli_command?: string | null
  api_base_url?: string | null
  api_model?: string | null
  api_key?: string | null
  api_key_set?: boolean
  api_key_hint?: string | null
}

export type AiSettingsMode = 'inherit' | 'custom' | 'disabled'

export interface ProjectAiSettings {
  mode: AiSettingsMode
  providers: ProviderItem[] | null
  default_provider_id: string | null
  effective: {
    source: string
    providers: ProviderItem[]
    default_provider_id: string | null
    registered_count: number
  }
  catalog?: unknown
}

export interface AiOverviewProvidersSummary {
  mode: AiSettingsMode
  total: number
  enabled: number
  default_provider_id: string | null
}

export interface AiOverviewEvaluationSummary {
  providers_with_current_result: number
  running: number
  failed: number
  stale: number
}

export interface AiOverviewGroupsSummary {
  total: number
  invalid: number
}

export interface AiOverviewError {
  section: 'providers' | 'evaluation' | 'ai_groups'
  code: string
}

export interface AiOverviewData {
  providers: AiOverviewProvidersSummary | null
  evaluation: AiOverviewEvaluationSummary | null
  ai_groups: AiOverviewGroupsSummary | null
  errors: AiOverviewError[]
}

export type EvaluationLifecycle = 'pending' | 'running' | 'completed' | 'failed' | 'cancelled'
export type EvaluationVerdict = 'suitable' | 'unsuitable' | null

export interface AiEvaluation {
  evaluation_id: string
  project_id: string
  provider_id: string
  provider_name?: string | null
  subject_fingerprint?: string
  lifecycle: EvaluationLifecycle
  verdict: EvaluationVerdict
  reason_code?: string | null
  diagnostic_summary?: string | null
  is_stale: boolean
  created_at: string
  started_at?: string | null
  finished_at?: string | null
}

export interface AiEvaluationCurrentResponse {
  provider_id: string
  current: AiEvaluation | null
  latest_history: AiEvaluation | null
}

export interface AiEvaluationHistoryResponse {
  items: AiEvaluation[]
  next_cursor: string | null
}

export interface AiGroupRole {
  provider_id: string
  provider_name: string | null
  is_available: boolean
}

export interface AiGroup {
  ai_group_id: string
  project_id: string
  name: string
  description: string | null
  work_executor: AiGroupRole | null
  reviewer: AiGroupRole | null
  reworker: AiGroupRole | null
  created_at?: string
  updated_at?: string
}

export interface AiGroupInput {
  name: string
  description?: string | null
  work_executor_provider_id: string
  reviewer_provider_id?: string | null
  reworker_provider_id?: string | null
}