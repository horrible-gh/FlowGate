// AI Settings types (v0.2 flowgate.v02.0015 T0003, product semantics flowgate.v02.0017 T0002)

export type ProviderExecType = 'cli' | 'api'

// One AI connection (internally still a "provider"; user-facing wording is "AI 연결").
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

// inherit = use the system settings, custom = project settings, disabled = no AI in this project.
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

// ── Connection test (isolated baseline probe; not a capability evaluation) ───────

export type ConnectionTestLifecycle = 'pending' | 'running' | 'completed' | 'failed' | 'cancelled'
export type ConnectionTestResult = 'responded' | 'no_response' | null

export interface AiConnectionTest {
  test_id: string
  project_id: string
  provider_id: string
  provider_name?: string | null
  lifecycle: ConnectionTestLifecycle
  result: ConnectionTestResult
  reason_code?: string | null
  diagnostic_summary?: string | null
  is_stale: boolean
  created_at: string
  started_at?: string | null
  finished_at?: string | null
}

export interface AiConnectionTestCurrent {
  provider_id: string
  current: AiConnectionTest | null
  latest_history: AiConnectionTest | null
}

// ── AI Evaluation (judge-scored Scratch task) ──────────────────────────────────

export type EvaluationLifecycle =
  | 'pending'
  | 'preparing'
  | 'running'
  | 'reviewing'
  | 'completed'
  | 'failed'
  | 'cancelled'

export const ACTIVE_EVALUATION_LIFECYCLES: EvaluationLifecycle[] = ['pending', 'preparing', 'running', 'reviewing']

export const EVALUATION_CRITERIA = [
  'instruction_understanding',
  'task_decomposition',
  'reasoning',
  'execution',
  'verification',
  'reporting',
] as const
export type EvaluationCriterion = (typeof EVALUATION_CRITERIA)[number]

export interface AiEvaluationParty {
  provider_id: string
  provider_name: string | null
}

export interface AiEvaluationScore {
  key: EvaluationCriterion | string
  description: string | null
  score: number | null
  rationale: string | null
}

export interface AiEvaluationArtifact {
  path: string
  size: number
  sha256: string | null
  origin: 'created' | 'modified' | 'unchanged'
  excerpt: string | null
}

// Optional human review of a completed evaluation. Its score, when given, is the final score.
export interface AiHumanReview {
  score: number | null
  comment: string | null
  reviewer_id: string | null
  reviewed_at: string
}

export interface AiEvaluation {
  evaluation_id: string
  project_id: string
  target: AiEvaluationParty
  judge: AiEvaluationParty
  lifecycle: EvaluationLifecycle
  task: { title: string | null; summary: string | null; instruction?: string | null }
  // total_score = judge AI, final_score = human score if reviewed, else total_score.
  total_score: number | null
  final_score: number | null
  human_review: AiHumanReview | null
  evaluation_set: { set_id: string; name: string | null } | null
  review_summary: string | null
  scores: AiEvaluationScore[]
  failure_stage?: string | null
  reason_code?: string | null
  diagnostic_summary?: string | null
  scratch_ref?: string | null
  target_report?: string | null
  artifacts?: AiEvaluationArtifact[]
  artifact_count?: number
  created_at: string
  started_at?: string | null
  finished_at?: string | null
}

// Scores are stored 0-100 and shown on the 10-point scale of 09-ai-settings.html (86 -> "8.6").
export function formatScore(score: number | null | undefined): string {
  return typeof score === 'number' ? (score / 10).toFixed(1) : '—'
}

export function scoreClass(score: number): string {
  if (score >= 80) return 'score-high'
  if (score >= 60) return 'score-mid'
  return 'score-low'
}

export interface AiEvaluationHistoryResponse {
  items: AiEvaluation[]
  next_cursor: string | null
}

// Reusable task: fixes instruction/constraints so several AIs run the same task.
export interface AiEvaluationSet {
  set_id: string
  name: string
  instruction: string
  constraints: string[]
  created_at?: string
  updated_at?: string
}

export interface AiEvaluationSetInput {
  name: string
  instruction: string
  constraints: string[]
}

// ── AI Group (team: members + custom roles + mapping, optional Supervisor) ──────

export type AiConnectionStatus = 'available' | 'unavailable' | 'broken'

export interface AiConnectionRef {
  provider_id: string
  provider_name: string | null
  is_available: boolean
  status: AiConnectionStatus
}

// Latest completed evaluation of a member's AI connection (null = not evaluated yet).
export interface AiLatestEvaluation {
  evaluation_id: string
  total_score: number | null
  finished_at: string | null
}

export interface AiGroupMember extends AiConnectionRef {
  member_id: string
  role_ids: string[]
  latest_evaluation?: AiLatestEvaluation | null
  // Latest evaluation scores on the criteria of the member's roles (0-100, null = no data).
  fit_score?: number | null
  created_at?: string
}

export interface AiGroupRole {
  role_id: string
  name: string
  description: string | null
  // Criteria the role relies on for member fit. Empty = all criteria.
  criteria: string[]
  position: number
  member_ids: string[]
}

export type RuntimeSlot = 'work_executor' | 'reviewer' | 'reworker'
export const RUNTIME_SLOTS: RuntimeSlot[] = ['work_executor', 'reviewer', 'reworker']

export interface AiGroup {
  ai_group_id: string
  project_id: string
  name: string
  description: string | null
  enabled: boolean
  is_default: boolean
  members: AiGroupMember[]
  roles: AiGroupRole[]
  supervisor: (AiConnectionRef & { member_id: string; latest_evaluation?: AiLatestEvaluation | null }) | null
  // Runtime execution slots used to pre-fill Start. Not the team's roles.
  runtime_binding: Record<RuntimeSlot, AiConnectionRef | null>
  member_count: number
  role_count: number
  status: 'ok' | 'empty' | 'attention'
  created_at?: string
  updated_at?: string
}

export interface AiGroupInput {
  name: string
  description?: string | null
}

export interface AiGroupUpdateInput extends AiGroupInput {
  supervisor_member_id?: string | null
}

export interface AiGroupRoleInput {
  name: string
  description?: string | null
  criteria?: string[]
}

export interface AiGroupStateInput {
  enabled?: boolean
  is_default?: boolean
}

// Repeat-count ceiling (system setting ai_repeat_count_max).
export interface AiExecutionPolicy {
  repeat_count_max: number
  repeat_count_min: number
  repeat_count_hard_max: number
}

export type AiGroupRuntimeBindingInput = Partial<Record<`${RuntimeSlot}_provider_id`, string | null>>

// ── Overview ───────────────────────────────────────────────────────────────────

export interface AiOverviewConnections {
  mode: AiSettingsMode
  source: string
  total: number
  usable: number
  default_provider_id: string | null
  items: { id: string; name: string; exec_type: ProviderExecType; kind: string; enabled: boolean; usable: boolean }[]
}

export interface AiOverviewEvaluation {
  active: number
  latest_score: number | null
  latest_at: string | null
  recent: Pick<
    AiEvaluation,
    | 'evaluation_id'
    | 'target'
    | 'judge'
    | 'lifecycle'
    | 'total_score'
    | 'final_score'
    | 'human_review'
    | 'evaluation_set'
    | 'scores'
    | 'review_summary'
    | 'created_at'
    | 'finished_at'
  >[]
}

export interface AiOverviewGroups {
  total: number
  attention: number
  items: (Pick<AiGroup, 'ai_group_id' | 'name' | 'member_count' | 'role_count' | 'supervisor' | 'status' | 'enabled' | 'is_default'> & {
    members: (AiConnectionRef & { member_id: string })[]
    roles: { role_id: string; name: string }[]
  })[]
}

export interface AiOverviewError {
  section: 'connections' | 'evaluation' | 'ai_groups'
  code: string
}

export interface AiOverviewData {
  connections: AiOverviewConnections | null
  evaluation: AiOverviewEvaluation | null
  ai_groups: AiOverviewGroups | null
  errors: AiOverviewError[]
}
