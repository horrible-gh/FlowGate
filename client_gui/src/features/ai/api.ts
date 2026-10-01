// AI API adapter (WP0002 / T0003, flowgate.v02.0017 T0002). Reuses `@shared/api` transport.
// AI evaluation and AI groups belong to no project (flowgate.v02.0017 CH0006 (4)); only the
// connection test still runs under a project.

import { deleteRequest, extractApiErrorMessage, getRequest, patchRequest, postRequest, putRequest } from '@shared/api'
import { normalizeApiError } from '@shared/apiErrors'
import i18n from '@shared/i18n'
import { PROVIDERS_MAX } from './limits'
import type {
  AiConnectionTest,
  AiConnectionTestCurrent,
  AiEvaluation,
  AiEvaluationHistoryResponse,
  AiEvaluationSet,
  AiEvaluationSetInput,
  AiGroup,
  AiGroupInput,
  AiGroupRoleInput,
  AiGroupStateInput,
  AiGroupUpdateInput,
  AiProbeResult,
  ProviderItem,
  SystemAiSettings,
  UserAiSettings,
  VisibleSystemAi,
} from './types'

const project = (projectId: string) => `/api/v1/projects/${encodeURIComponent(projectId)}`
const API = '/api/v1'

/** One rejected field of a 422 validation failure ({field, reason, index?, max?}). */
export interface AiFieldError {
  field: string
  reason: string
  index?: number
  max?: number
}

/** A failed AI request: the localized message plus the server code and per-field reasons, so a
 *  form can point at the field that was rejected. */
export class AiRequestError extends Error {
  constructor(message: string, readonly code: string | null, readonly fieldErrors: AiFieldError[]) {
    super(message)
    this.name = 'AiRequestError'
  }
}

function fieldErrorsOf(source: Readonly<Record<string, unknown>>): AiFieldError[] {
  const raw = source.errors
  if (!Array.isArray(raw)) return []
  return raw.flatMap((entry) => {
    if (!entry || typeof entry !== 'object') return []
    const { field, reason, index, max } = entry as Record<string, unknown>
    if (typeof field !== 'string' || typeof reason !== 'string') return []
    return [{
      field,
      reason,
      ...(typeof index === 'number' ? { index } : {}),
      ...(typeof max === 'number' ? { max } : {}),
    }]
  })
}

// The AI routes answer {"error": {"code"}}; show the localized reason for a known code
// (gui.ai.errors.<code>) instead of one generic "failed" line, and keep an unknown code visible.
function aiErrorMessage(error: unknown, fallback: string, fieldErrors: AiFieldError[]): string {
  const { code, network } = normalizeApiError(error)
  if (!network && code) {
    if (code === 'validation_failed' && fieldErrors.some((e) => e.field === 'providers' && e.reason === 'too_many')) {
      return String(i18n.global.t('gui.ai.owned.limit_reached', { max: PROVIDERS_MAX }))
    }
    const key = `gui.ai.errors.${code}`
    if (i18n.global.te(key)) return String(i18n.global.t(key))
    return `${extractApiErrorMessage(error, fallback)} (${code})`
  }
  return extractApiErrorMessage(error, fallback)
}

async function call<T>(request: () => Promise<{ data: T }>, fallback: string): Promise<T> {
  try {
    return (await request()).data
  } catch (error) {
    const { code, source } = normalizeApiError(error)
    const fieldErrors = fieldErrorsOf(source)
    throw new AiRequestError(aiErrorMessage(error, fallback, fieldErrors), code, fieldErrors)
  }
}

export interface SequenceStep {
  item_seq: number
  type?: string | null
  status?: string | null
}

export async function listSequenceSteps(docRef: string): Promise<SequenceStep[]> {
  const data = await call(
    () => getRequest<{ items?: SequenceStep[] }>('/api/v1/workflow/sequence', { doc_id: docRef }),
    'Failed to load the workflow sequence.',
  )
  return data.items ?? []
}

// ── Owned AI lists: 시스템 AI (administrator) / 내 AI (the signed-in user) ────────

export async function fetchSystemAi(): Promise<SystemAiSettings> {
  return call(() => getRequest<SystemAiSettings>('/api/v1/system/ai-settings'), 'Failed to load system AI.')
}

export async function saveSystemAi(providers: ProviderItem[], defaultProviderId: string | null): Promise<SystemAiSettings> {
  return call(
    () => putRequest<SystemAiSettings>('/api/v1/system/ai-settings', { providers, default_provider_id: defaultProviderId }),
    'Failed to save system AI.',
  )
}

export async function probeSystemAi(provider: ProviderItem): Promise<AiProbeResult> {
  return call(
    () => postRequest<AiProbeResult>('/api/v1/system/ai-settings/test-provider', {
      exec_type: provider.exec_type,
      kind: provider.kind,
      cli_command: provider.cli_command ?? null,
      api_base_url: provider.api_base_url ?? null,
    }),
    String(i18n.global.t('gui.ai.providers.test_start_failed')),
  )
}

// Read-only system AI for users who are not administrators (the AI 관리 list shows both owners).
export async function fetchVisibleSystemAi(): Promise<VisibleSystemAi> {
  return call(() => getRequest<VisibleSystemAi>('/api/v1/system/ai-providers'), 'Failed to load system AI.')
}

export async function fetchMyAi(): Promise<UserAiSettings> {
  return call(() => getRequest<UserAiSettings>('/api/v1/me/ai-providers'), 'Failed to load my AI.')
}

export async function saveMyAi(providers: ProviderItem[]): Promise<UserAiSettings> {
  return call(() => putRequest<UserAiSettings>('/api/v1/me/ai-providers', { providers }), 'Failed to save my AI.')
}

// null = follow the system default.
export async function saveMyAiDefault(providerId: string | null): Promise<UserAiSettings> {
  return call(
    () => putRequest<UserAiSettings>('/api/v1/me/ai-default', { provider_id: providerId }),
    'Failed to save my default AI.',
  )
}

export async function startConnectionTest(projectId: string, providerId: string): Promise<AiConnectionTest> {
  return call(
    () => postRequest<AiConnectionTest>(`${project(projectId)}/ai-connection-tests`, { provider_id: providerId }),
    String(i18n.global.t('gui.ai.providers.test_start_failed')),
  )
}

export async function fetchCurrentConnectionTest(projectId: string, providerId: string): Promise<AiConnectionTestCurrent> {
  return call(
    () => getRequest<AiConnectionTestCurrent>(`${project(projectId)}/ai-connection-tests/current`, { provider_id: providerId }),
    'Failed to load the connection test.',
  )
}

// Repeat-count ceiling: the system setting ai_repeat_count_max (system.settings.manage).
export async function saveRepeatCountMax(value: number): Promise<void> {
  await call(
    () => patchRequest('/api/v1/system/settings', { updates: { ai_repeat_count_max: String(value) } }),
    'Failed to save the repeat limit.',
  )
}

// ── AI Evaluation ──────────────────────────────────────────────────────────────

export interface StartEvaluationInput {
  targetProviderId: string
  judgeProviderId: string
  evaluationSetId?: string | null
  locale?: string
}

export async function startEvaluation(input: StartEvaluationInput): Promise<AiEvaluation> {
  return call(
    () => postRequest<AiEvaluation>(`${API}/ai-evaluations`, {
      target_provider_id: input.targetProviderId,
      judge_provider_id: input.judgeProviderId,
      evaluation_set_id: input.evaluationSetId || null,
      locale: input.locale ?? null,
    }),
    String(i18n.global.t('gui.ai.evaluation.start_failed')),
  )
}

export async function fetchEvaluationHistory(
  targetProviderId?: string,
  limit = 20,
  cursor?: string | null,
): Promise<AiEvaluationHistoryResponse> {
  const params: Record<string, unknown> = { limit }
  if (targetProviderId) params.target_provider_id = targetProviderId
  if (cursor) params.cursor = cursor
  return call(
    () => getRequest<AiEvaluationHistoryResponse>(`${API}/ai-evaluations`, params),
    'Failed to load evaluation history.',
  )
}

export async function fetchEvaluationDetail(evaluationId: string): Promise<AiEvaluation> {
  return call(
    () => getRequest<AiEvaluation>(`${API}/ai-evaluations/${encodeURIComponent(evaluationId)}`),
    'Failed to load evaluation details.',
  )
}

export async function cancelEvaluation(evaluationId: string): Promise<AiEvaluation> {
  return call(
    () => postRequest<AiEvaluation>(`${API}/ai-evaluations/${encodeURIComponent(evaluationId)}/cancel`, {}),
    'Failed to cancel evaluation.',
  )
}

export async function saveHumanReview(
  evaluationId: string,
  input: { score: number | null; comment: string | null },
): Promise<AiEvaluation> {
  return call(
    () => putRequest<AiEvaluation>(`${API}/ai-evaluations/${encodeURIComponent(evaluationId)}/human-review`, input),
    'Failed to save the human review.',
  )
}

export async function listEvaluationSets(): Promise<AiEvaluationSet[]> {
  const data = await call(
    () => getRequest<{ items: AiEvaluationSet[] }>(`${API}/ai-evaluation-sets`),
    'Failed to load evaluation sets.',
  )
  return data?.items ?? []
}

export async function saveEvaluationSet(
  setId: string | null,
  input: AiEvaluationSetInput,
): Promise<AiEvaluationSet> {
  const base = `${API}/ai-evaluation-sets`
  return call(
    () => (setId
      ? putRequest<AiEvaluationSet>(`${base}/${encodeURIComponent(setId)}`, input)
      : postRequest<AiEvaluationSet>(base, input)),
    'Failed to save the evaluation set.',
  )
}

export async function deleteEvaluationSet(setId: string): Promise<void> {
  await call(
    () => deleteRequest(`${API}/ai-evaluation-sets/${encodeURIComponent(setId)}`),
    'Failed to delete the evaluation set.',
  )
}

// ── AI Group ───────────────────────────────────────────────────────────────────

const group = (groupId: string) => `${API}/ai-groups/${encodeURIComponent(groupId)}`

export async function listAiGroups(): Promise<AiGroup[]> {
  const data = await call(() => getRequest<{ items: AiGroup[] }>(`${API}/ai-groups`), 'Failed to load AI groups.')
  return data?.items ?? []
}

export async function getAiGroup(groupId: string): Promise<AiGroup> {
  return call(() => getRequest<AiGroup>(group(groupId)), 'Failed to load AI group.')
}

export async function createAiGroup(input: AiGroupInput): Promise<AiGroup> {
  return call(() => postRequest<AiGroup>(`${API}/ai-groups`, input), 'Failed to create AI group.')
}

export async function updateAiGroup(groupId: string, input: AiGroupUpdateInput): Promise<AiGroup> {
  return call(() => putRequest<AiGroup>(group(groupId), input), 'Failed to update AI group.')
}

export async function setAiGroupState(groupId: string, input: AiGroupStateInput): Promise<AiGroup> {
  return call(() => putRequest<AiGroup>(`${group(groupId)}/state`, input), 'Failed to update AI group.')
}

export async function deleteAiGroup(groupId: string): Promise<void> {
  await call(() => deleteRequest(group(groupId)), 'Failed to delete AI group.')
}

export async function addAiGroupMember(groupId: string, providerId: string): Promise<AiGroup> {
  return call(
    () => postRequest<AiGroup>(`${group(groupId)}/members`, { provider_id: providerId }),
    'Failed to add the member.',
  )
}

export async function removeAiGroupMember(groupId: string, memberId: string): Promise<AiGroup> {
  return call(
    () => deleteRequest<AiGroup>(`${group(groupId)}/members/${encodeURIComponent(memberId)}`),
    'Failed to remove the member.',
  )
}

export async function setAiGroupMemberRoles(
  groupId: string,
  memberId: string,
  roleIds: string[],
): Promise<AiGroup> {
  return call(
    () => putRequest<AiGroup>(`${group(groupId)}/members/${encodeURIComponent(memberId)}/roles`, { role_ids: roleIds }),
    'Failed to update the role mapping.',
  )
}

export async function createAiGroupRole(groupId: string, input: AiGroupRoleInput): Promise<AiGroup> {
  return call(() => postRequest<AiGroup>(`${group(groupId)}/roles`, input), 'Failed to add the role.')
}

export async function updateAiGroupRole(
  groupId: string,
  roleId: string,
  input: AiGroupRoleInput,
): Promise<AiGroup> {
  return call(
    () => putRequest<AiGroup>(`${group(groupId)}/roles/${encodeURIComponent(roleId)}`, input),
    'Failed to update the role.',
  )
}

export async function deleteAiGroupRole(groupId: string, roleId: string): Promise<AiGroup> {
  return call(
    () => deleteRequest<AiGroup>(`${group(groupId)}/roles/${encodeURIComponent(roleId)}`),
    'Failed to delete the role.',
  )
}
