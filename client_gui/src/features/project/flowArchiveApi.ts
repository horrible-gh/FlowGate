// Display-only archive. Kept separate from Git archive/restore APIs.
import { getRequest, postRequest } from '@shared/api'
import { toGraphError } from './flowGraphApi'

export interface FlowArchiveState {
  archived: boolean
  archivedAt: string | null
  archivedBy: string | null
  eligible: boolean
  reason: string | null
}

function state(raw: any): FlowArchiveState {
  if (raw?.ok !== true || typeof raw.archived !== 'boolean' || typeof raw.eligible !== 'boolean') {
    throw new Error('Invalid flow archive response')
  }
  return { archived: raw.archived, archivedAt: raw.archived_at ?? null, archivedBy: raw.archived_by ?? null,
    eligible: raw.eligible, reason: raw.reason ?? null }
}

export async function readFlowArchive(flowId: string): Promise<FlowArchiveState> {
  try {
    return state((await getRequest(`/api/v1/flows/${encodeURIComponent(flowId)}/archive`)).data)
  } catch (error) { throw toGraphError(error, '') }
}

export async function changeFlowArchive(flowId: string, archived: boolean): Promise<FlowArchiveState> {
  try {
    return state((await postRequest(`/api/v1/flows/${encodeURIComponent(flowId)}/${archived ? 'archive' : 'restore'}`, {})).data)
  } catch (error) { throw toGraphError(error, '') }
}
