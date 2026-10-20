import { ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { listAllProjects, SettingsRequestError } from '../api'
import type { ProjectModuleRef, ProjectRow } from '../types'
import { STALE, useLiveRequests } from './useLiveRequests'

// Every current project list consumer drops this row (features/project/api.ts, NR0003 §3.1): it is
// a bookkeeping project, not one a person can select.
const SYSTEM_PROJECT_ID = '__SYSTEM__'

/** How a failed read is shown: 401 and 403 are 접근 불가, never an empty list or 미설정. */
export interface ReadError {
  kind: 'unauthorized' | 'forbidden' | 'failed'
  message: string
}

export function readErrorOf(err: unknown): ReadError {
  const status = err instanceof SettingsRequestError ? err.status : null
  const message = err instanceof Error ? err.message : String(err)
  if (status === 401) return { kind: 'unauthorized', message }
  if (status === 403) return { kind: 'forbidden', message }
  return { kind: 'failed', message }
}

function toModules(value: unknown): ProjectModuleRef[] | null {
  if (value === undefined || value === null) return []
  if (!Array.isArray(value)) return null
  const modules: ProjectModuleRef[] = []
  for (const item of value) {
    const m = item as Partial<ProjectModuleRef> | null
    if (!m || typeof m.name !== 'string') return null
    modules.push({ name: m.name, title: typeof m.title === 'string' && m.title ? m.title : m.name })
  }
  return modules
}

/** The rows of GET /projects as sent, or null when the answer does not have the contract's shape. */
export function toProjectRows(value: unknown): ProjectRow[] | null {
  if (!Array.isArray(value)) return null
  const rows: ProjectRow[] = []
  for (const item of value) {
    const p = item as Record<string, unknown> | null
    if (!p || typeof p.project_id !== 'string' || typeof p.project_name !== 'string') return null
    const active = p.is_active
    if (active !== 0 && active !== 1 && typeof active !== 'boolean') return null
    const modules = toModules(p.modules)
    if (!modules) return null
    if (p.project_id === SYSTEM_PROJECT_ID) continue
    rows.push({
      project_id: p.project_id,
      project_name: p.project_name,
      description: typeof p.description === 'string' ? p.description : null,
      is_active: active === true || active === 1,
      modules,
    })
  }
  return rows
}

/**
 * The project list of the 프로젝트 selection area and 시스템 › 전체 프로젝트 (flowgate.v02.0076 T#5,
 * D §2.3 / §2.8): GET /projects?status=all only. `projects` is null while nothing is confirmed —
 * loading, failed, or the session / role went away — and an array (maybe empty) only for an answer
 * the server actually sent. A failure clears the list and keeps the error; it never turns into an
 * empty list. Retrying repeats this GET only. `lost` is true once the session went away (see
 * useLiveRequests): the list is gone with every section view, and no GET is sent until it is back.
 */
export function useProjectList() {
  const { t } = useI18n()
  const projects = ref<ProjectRow[] | null>(null)
  const loading = ref(false)
  const error = ref<ReadError | null>(null)

  const requests = useLiveRequests(() => {
    projects.value = null
    loading.value = false
    error.value = null
  })

  async function load(): Promise<boolean> {
    // No token or no administrator access any more: nothing is sent, and every project view is ended.
    const at = requests.begin()
    if (at === null) return false
    loading.value = true
    error.value = null
    try {
      const rows = toProjectRows(await requests.live(at, listAllProjects()))
      if (!rows) throw new Error(t('gui.settings.projects.errors.bad_response'))
      projects.value = rows
      return true
    } catch (err) {
      if (err === STALE) return false
      projects.value = null
      error.value = readErrorOf(err)
      return false
    } finally {
      if (at === requests.current()) loading.value = false
    }
  }

  return { projects, loading, error, lost: requests.lost, load }
}
