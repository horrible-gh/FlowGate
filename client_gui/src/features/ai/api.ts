// AI API adapter (WP0002 / T0003, flowgate.v02.0017 T0002). Reuses `@shared/api` transport.

import { deleteRequest, extractApiErrorMessage, getRequest, patchRequest, postRequest, putRequest } from '@shared/api'
import type {
  AiConnectionTest,
  AiConnectionTestCurrent,
  AiEvaluation,
  AiEvaluationHistoryResponse,
  AiEvaluationSet,
  AiEvaluationSetInput,
  AiExecutionPolicy,
  AiGroup,
  AiGroupInput,
  AiGroupRoleInput,
  AiGroupRuntimeBindingInput,
  AiGroupStateInput,
  AiGroupUpdateInput,
  AiOverviewData,
  AiSettingsMode,
  ProjectAiSettings,
  ProviderItem,
} from './types'

const project = (projectId: string) => `/api/v1/projects/${encodeURIComponent(projectId)}`

async function call<T>(request: () => Promise<{ data: T }>, fallback: string): Promise<T> {
  try {
    return (await request()).data
  } catch (error) {
    throw new Error(extractApiErrorMessage(error, fallback))
  }
}

export async function fetchAiOverview(projectId: string): Promise<AiOverviewData> {
  return call(() => getRequest<AiOverviewData>(`${project(projectId)}/ai-overview`), 'Failed to load AI overview.')
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

// ── AI connections (project AI settings) ─────────────────────────────────────

export async function fetchProjectAiSettings(projectId: string): Promise<ProjectAiSettings> {
  return call(() => getRequest<ProjectAiSettings>(`${project(projectId)}/ai-settings`), 'Failed to load AI settings.')
}

export interface SaveAiSettingsInput {
  mode: AiSettingsMode
  providers?: ProviderItem[] | null
  default_provider_id?: string | null
}

export async function saveProjectAiSettings(projectId: string, input: SaveAiSettingsInput): Promise<ProjectAiSettings> {
  return call(() => putRequest<ProjectAiSettings>(`${project(projectId)}/ai-settings`, input), 'Failed to save AI settings.')
}

export async function startConnectionTest(projectId: string, providerId: string): Promise<AiConnectionTest> {
  return call(
    () => postRequest<AiConnectionTest>(`${project(projectId)}/ai-connection-tests`, { provider_id: providerId }),
    'Failed to start the connection test.',
  )
}

export async function fetchCurrentConnectionTest(projectId: string, providerId: string): Promise<AiConnectionTestCurrent> {
  return call(
    () => getRequest<AiConnectionTestCurrent>(`${project(projectId)}/ai-connection-tests/current`, { provider_id: providerId }),
    'Failed to load the connection test.',
  )
}

// Repeat-count ceiling: read per project, saved as the system setting (system.settings.manage).
export async function fetchExecutionPolicy(projectId: string): Promise<AiExecutionPolicy> {
  return call(() => getRequest<AiExecutionPolicy>(`${project(projectId)}/ai-execution-policy`), 'Failed to load the repeat limit.')
}

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

export async function startEvaluation(projectId: string, input: StartEvaluationInput): Promise<AiEvaluation> {
  return call(
    () => postRequest<AiEvaluation>(`${project(projectId)}/ai-evaluations`, {
      target_provider_id: input.targetProviderId,
      judge_provider_id: input.judgeProviderId,
      evaluation_set_id: input.evaluationSetId || null,
      locale: input.locale ?? null,
    }),
    'Failed to start the evaluation.',
  )
}

export async function fetchEvaluationHistory(
  projectId: string,
  targetProviderId?: string,
  limit = 20,
  cursor?: string | null,
): Promise<AiEvaluationHistoryResponse> {
  const params: Record<string, unknown> = { limit }
  if (targetProviderId) params.target_provider_id = targetProviderId
  if (cursor) params.cursor = cursor
  return call(
    () => getRequest<AiEvaluationHistoryResponse>(`${project(projectId)}/ai-evaluations`, params),
    'Failed to load evaluation history.',
  )
}

export async function fetchEvaluationDetail(projectId: string, evaluationId: string): Promise<AiEvaluation> {
  return call(
    () => getRequest<AiEvaluation>(`${project(projectId)}/ai-evaluations/${encodeURIComponent(evaluationId)}`),
    'Failed to load evaluation details.',
  )
}

export async function cancelEvaluation(projectId: string, evaluationId: string): Promise<AiEvaluation> {
  return call(
    () => postRequest<AiEvaluation>(`${project(projectId)}/ai-evaluations/${encodeURIComponent(evaluationId)}/cancel`, {}),
    'Failed to cancel evaluation.',
  )
}

export async function saveHumanReview(
  projectId: string,
  evaluationId: string,
  input: { score: number | null; comment: string | null },
): Promise<AiEvaluation> {
  return call(
    () => putRequest<AiEvaluation>(`${project(projectId)}/ai-evaluations/${encodeURIComponent(evaluationId)}/human-review`, input),
    'Failed to save the human review.',
  )
}

export async function listEvaluationSets(projectId: string): Promise<AiEvaluationSet[]> {
  const data = await call(
    () => getRequest<{ items: AiEvaluationSet[] }>(`${project(projectId)}/ai-evaluation-sets`),
    'Failed to load evaluation sets.',
  )
  return data?.items ?? []
}

export async function saveEvaluationSet(
  projectId: string,
  setId: string | null,
  input: AiEvaluationSetInput,
): Promise<AiEvaluationSet> {
  const base = `${project(projectId)}/ai-evaluation-sets`
  return call(
    () => (setId
      ? putRequest<AiEvaluationSet>(`${base}/${encodeURIComponent(setId)}`, input)
      : postRequest<AiEvaluationSet>(base, input)),
    'Failed to save the evaluation set.',
  )
}

export async function deleteEvaluationSet(projectId: string, setId: string): Promise<void> {
  await call(
    () => deleteRequest(`${project(projectId)}/ai-evaluation-sets/${encodeURIComponent(setId)}`),
    'Failed to delete the evaluation set.',
  )
}

// ── AI Group ───────────────────────────────────────────────────────────────────

const group = (projectId: string, groupId: string) => `${project(projectId)}/ai-groups/${encodeURIComponent(groupId)}`

export async function listAiGroups(projectId: string): Promise<AiGroup[]> {
  const data = await call(() => getRequest<{ items: AiGroup[] }>(`${project(projectId)}/ai-groups`), 'Failed to load AI groups.')
  return data?.items ?? []
}

export async function getAiGroup(projectId: string, groupId: string): Promise<AiGroup> {
  return call(() => getRequest<AiGroup>(group(projectId, groupId)), 'Failed to load AI group.')
}

export async function createAiGroup(projectId: string, input: AiGroupInput): Promise<AiGroup> {
  return call(() => postRequest<AiGroup>(`${project(projectId)}/ai-groups`, input), 'Failed to create AI group.')
}

export async function updateAiGroup(projectId: string, groupId: string, input: AiGroupUpdateInput): Promise<AiGroup> {
  return call(() => putRequest<AiGroup>(group(projectId, groupId), input), 'Failed to update AI group.')
}

export async function setAiGroupState(projectId: string, groupId: string, input: AiGroupStateInput): Promise<AiGroup> {
  return call(() => putRequest<AiGroup>(`${group(projectId, groupId)}/state`, input), 'Failed to update AI group.')
}

export async function deleteAiGroup(projectId: string, groupId: string): Promise<void> {
  await call(() => deleteRequest(group(projectId, groupId)), 'Failed to delete AI group.')
}

export async function addAiGroupMember(projectId: string, groupId: string, providerId: string): Promise<AiGroup> {
  return call(
    () => postRequest<AiGroup>(`${group(projectId, groupId)}/members`, { provider_id: providerId }),
    'Failed to add the member.',
  )
}

export async function removeAiGroupMember(projectId: string, groupId: string, memberId: string): Promise<AiGroup> {
  return call(
    () => deleteRequest<AiGroup>(`${group(projectId, groupId)}/members/${encodeURIComponent(memberId)}`),
    'Failed to remove the member.',
  )
}

export async function setAiGroupMemberRoles(
  projectId: string,
  groupId: string,
  memberId: string,
  roleIds: string[],
): Promise<AiGroup> {
  return call(
    () => putRequest<AiGroup>(`${group(projectId, groupId)}/members/${encodeURIComponent(memberId)}/roles`, { role_ids: roleIds }),
    'Failed to update the role mapping.',
  )
}

export async function createAiGroupRole(projectId: string, groupId: string, input: AiGroupRoleInput): Promise<AiGroup> {
  return call(() => postRequest<AiGroup>(`${group(projectId, groupId)}/roles`, input), 'Failed to add the role.')
}

export async function updateAiGroupRole(
  projectId: string,
  groupId: string,
  roleId: string,
  input: AiGroupRoleInput,
): Promise<AiGroup> {
  return call(
    () => putRequest<AiGroup>(`${group(projectId, groupId)}/roles/${encodeURIComponent(roleId)}`, input),
    'Failed to update the role.',
  )
}

export async function deleteAiGroupRole(projectId: string, groupId: string, roleId: string): Promise<AiGroup> {
  return call(
    () => deleteRequest<AiGroup>(`${group(projectId, groupId)}/roles/${encodeURIComponent(roleId)}`),
    'Failed to delete the role.',
  )
}

export async function setAiGroupRuntimeBinding(
  projectId: string,
  groupId: string,
  input: AiGroupRuntimeBindingInput,
): Promise<AiGroup> {
  return call(
    () => putRequest<AiGroup>(`${group(projectId, groupId)}/runtime-binding`, input),
    'Failed to save the runtime slots.',
  )
}
