// Project API adapter (WP0005 T#1 §1/§3). Reuses the existing `@shared/api` transport
// (same helper client/src's v0.1 screens already call) instead of a new client — this file
// only adds the request shape + snake_case-to-frontend-model normalization boundary that
// D0004 §1.2/§4.3 assigns to features/project. Scope is exactly the three endpoints WP0005
// T#1 §3 allows: active project list, create, and identifier suggestion. archive/restore/
// status/source-path/Git endpoints are out of this T's scope and are not called here.

import { extractApiErrorMessage, getRequest, postRequest } from '@shared/api'
import type { CreateProjectInput, Project, ProjectModule } from './types'

interface RawProjectModule {
  name: string
  title?: string | null
}

interface RawProject {
  project_id: string
  project_name: string
  description?: string | null
  color?: string | null
  is_active: number | boolean
  created_at?: string | null
  updated_at?: string | null
  modules?: RawProjectModule[] | null
}

interface RawProjectListResponse {
  projects?: RawProject[]
}

// Every current backend/v0.1 list response filters this row out (NR0003 §3.1) — it is a
// bookkeeping project, not a real one a user can select.
const SYSTEM_PROJECT_ID = '__SYSTEM__'

function toProjectModule(raw: RawProjectModule): ProjectModule {
  return { name: raw.name, title: raw.title || raw.name }
}

function toProject(raw: RawProject): Project {
  return {
    id: raw.project_id,
    name: raw.project_name,
    description: raw.description ?? '',
    color: raw.color ?? null,
    isActive: raw.is_active === 1 || raw.is_active === true,
    createdAt: raw.created_at ?? null,
    updatedAt: raw.updated_at ?? null,
    modules: Array.isArray(raw.modules) ? raw.modules.map(toProjectModule) : [],
  }
}

/** GET /api/v1/projects?status=active, normalized and with the system row dropped. */
export async function fetchActiveProjects(): Promise<Project[]> {
  try {
    const response = await getRequest<RawProjectListResponse | RawProject[]>(
      '/api/v1/projects',
      { status: 'active' },
    )
    const raw = Array.isArray(response.data) ? response.data : response.data?.projects ?? []
    return raw.filter((p) => p.project_id !== SYSTEM_PROJECT_ID).map(toProject)
  } catch (error) {
    throw new Error(extractApiErrorMessage(error, 'Failed to load projects.'))
  }
}

/**
 * POST /api/v1/projects with exactly the fields the backend reads (project_name/
 * project_id/description/color) — the v0.1 create form's unconnected decorative fields
 * (structure/storage type/custom path/members) are not part of this input type at all
 * (D0004 §2.4, NR0003 §3.3).
 */
export async function createProject(input: CreateProjectInput): Promise<Project> {
  try {
    const response = await postRequest<RawProject>('/api/v1/projects', {
      project_name: input.name,
      project_id: input.id,
      description: input.description ?? null,
      color: input.color ?? null,
    })
    return toProject(response.data)
  } catch (error) {
    throw new Error(extractApiErrorMessage(error, 'Failed to create project.'))
  }
}

/** POST /api/v1/slug/romanize — the existing project-id suggestion endpoint (NR0003 §3.3). */
export async function suggestProjectSlug(text: string): Promise<string | null> {
  const trimmed = text.trim()
  if (!trimmed) return null
  try {
    const response = await postRequest<{ suggested?: string | null }>(
      '/api/v1/slug/romanize',
      { text: trimmed },
    )
    return response.data?.suggested || null
  } catch (error) {
    throw new Error(extractApiErrorMessage(error, 'Failed to suggest a project id.'))
  }
}
