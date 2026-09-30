// AI API adapter (WP0002 / T0003). Reuses `@shared/api` transport.

import { deleteRequest, extractApiErrorMessage, getRequest, postRequest, putRequest } from '@shared/api'
import type {
  AiEvaluation,
  AiEvaluationCurrentResponse,
  AiEvaluationHistoryResponse,
  AiGroup,
  AiGroupInput,
  AiOverviewData,
  AiSettingsMode,
  ProjectAiSettings,
  ProviderItem,
} from './types'

export async function fetchAiOverview(projectId: string): Promise<AiOverviewData> {
  try {
    const res = await getRequest<AiOverviewData>(
      `/api/v1/projects/${encodeURIComponent(projectId)}/ai-overview`
    )
    return res.data
  } catch (error) {
    throw new Error(extractApiErrorMessage(error, 'Failed to load AI overview.'))
  }
}

export async function fetchProjectAiSettings(projectId: string): Promise<ProjectAiSettings> {
  try {
    const res = await getRequest<ProjectAiSettings>(
      `/api/v1/projects/${encodeURIComponent(projectId)}/ai-settings`
    )
    return res.data
  } catch (error) {
    throw new Error(extractApiErrorMessage(error, 'Failed to load AI settings.'))
  }
}

export interface SaveAiSettingsInput {
  mode: AiSettingsMode
  providers?: ProviderItem[] | null
  default_provider_id?: string | null
}

export async function saveProjectAiSettings(
  projectId: string,
  input: SaveAiSettingsInput
): Promise<ProjectAiSettings> {
  try {
    const res = await putRequest<ProjectAiSettings>(
      `/api/v1/projects/${encodeURIComponent(projectId)}/ai-settings`,
      input
    )
    return res.data
  } catch (error) {
    throw new Error(extractApiErrorMessage(error, 'Failed to save AI settings.'))
  }
}

export async function startEvaluation(projectId: string, providerId: string): Promise<AiEvaluation> {
  const res = await postRequest<AiEvaluation>(
    `/api/v1/projects/${encodeURIComponent(projectId)}/ai-evaluations`,
    { provider_id: providerId }
  )
  return res.data
}

export async function fetchCurrentEvaluation(
  projectId: string,
  providerId: string
): Promise<AiEvaluationCurrentResponse> {
  try {
    const res = await getRequest<AiEvaluationCurrentResponse>(
      `/api/v1/projects/${encodeURIComponent(projectId)}/ai-evaluations/current`,
      { provider_id: providerId }
    )
    return res.data
  } catch (error) {
    throw new Error(extractApiErrorMessage(error, 'Failed to load current evaluation.'))
  }
}

export async function fetchEvaluationHistory(
  projectId: string,
  providerId?: string,
  limit = 20,
  cursor?: string | null
): Promise<AiEvaluationHistoryResponse> {
  const params: Record<string, unknown> = { limit }
  if (providerId) params.provider_id = providerId
  if (cursor) params.cursor = cursor
  try {
    const res = await getRequest<AiEvaluationHistoryResponse>(
      `/api/v1/projects/${encodeURIComponent(projectId)}/ai-evaluations`,
      params
    )
    return res.data
  } catch (error) {
    throw new Error(extractApiErrorMessage(error, 'Failed to load evaluation history.'))
  }
}

export async function fetchEvaluationDetail(
  projectId: string,
  evaluationId: string
): Promise<AiEvaluation> {
  try {
    const res = await getRequest<AiEvaluation>(
      `/api/v1/projects/${encodeURIComponent(projectId)}/ai-evaluations/${encodeURIComponent(evaluationId)}`
    )
    return res.data
  } catch (error) {
    throw new Error(extractApiErrorMessage(error, 'Failed to load evaluation details.'))
  }
}

export async function cancelEvaluation(projectId: string, evaluationId: string): Promise<void> {
  try {
    await postRequest(
      `/api/v1/projects/${encodeURIComponent(projectId)}/ai-evaluations/${encodeURIComponent(evaluationId)}/cancel`,
      {}
    )
  } catch (error) {
    throw new Error(extractApiErrorMessage(error, 'Failed to cancel evaluation.'))
  }
}

export async function listAiGroups(projectId: string): Promise<AiGroup[]> {
  try {
    const res = await getRequest<{ items: AiGroup[] }>(
      `/api/v1/projects/${encodeURIComponent(projectId)}/ai-groups`
    )
    return res.data?.items ?? []
  } catch (error) {
    throw new Error(extractApiErrorMessage(error, 'Failed to load AI groups.'))
  }
}

export async function getAiGroup(projectId: string, groupId: string): Promise<AiGroup> {
  try {
    const res = await getRequest<AiGroup>(
      `/api/v1/projects/${encodeURIComponent(projectId)}/ai-groups/${encodeURIComponent(groupId)}`
    )
    return res.data
  } catch (error) {
    throw new Error(extractApiErrorMessage(error, 'Failed to load AI group.'))
  }
}

export async function createAiGroup(projectId: string, input: AiGroupInput): Promise<AiGroup> {
  try {
    const res = await postRequest<AiGroup>(
      `/api/v1/projects/${encodeURIComponent(projectId)}/ai-groups`,
      input
    )
    return res.data
  } catch (error) {
    throw new Error(extractApiErrorMessage(error, 'Failed to create AI group.'))
  }
}

export async function updateAiGroup(
  projectId: string,
  groupId: string,
  input: AiGroupInput
): Promise<AiGroup> {
  try {
    const res = await putRequest<AiGroup>(
      `/api/v1/projects/${encodeURIComponent(projectId)}/ai-groups/${encodeURIComponent(groupId)}`,
      input
    )
    return res.data
  } catch (error) {
    throw new Error(extractApiErrorMessage(error, 'Failed to update AI group.'))
  }
}

export async function deleteAiGroup(projectId: string, groupId: string): Promise<void> {
  try {
    await deleteRequest(
      `/api/v1/projects/${encodeURIComponent(projectId)}/ai-groups/${encodeURIComponent(groupId)}`
    )
  } catch (error) {
    throw new Error(extractApiErrorMessage(error, 'Failed to delete AI group.'))
  }
}