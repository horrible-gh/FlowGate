import { postRequest } from './api'

export interface ProjectStateResponse {
  project_id: string
  project_name: string
  description?: string | null
  color?: string | null
  is_active: number
  created_at?: string
  updated_at?: string
  modules?: Array<{ name: string; title: string }>
}

export async function setProjectArchiveState(
  projectId: string,
  archived: boolean,
): Promise<ProjectStateResponse> {
  const action = archived ? 'archive' : 'restore'
  const { data } = await postRequest<ProjectStateResponse>(
    `/api/v1/projects/${encodeURIComponent(projectId)}/${action}`,
    {},
  )
  return data
}
