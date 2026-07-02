import { computed, reactive } from 'vue'
import { defineStore } from 'pinia'
import { getRequest } from '@shared/api'
import { useProjectStore } from './project'

export type DashboardActivityType =
  | 'document_created'
  | 'document_edited'
  | 'document_state_changed'
  | 'workflow_state_changed'
  | 'question_answered'
  | 'group_approved'

export interface DashboardNavigation {
  kind: 'document' | 'group' | 'none'
  doc_id?: string | null
  group_id?: string | null
}

export interface DashboardActivity {
  event_id: number
  activity_type: DashboardActivityType | string
  occurred_at: string
  actor: { user_id: string; username: string } | null
  group: { group_id: string; title: string } | null
  document: {
    doc_id: string
    type_code: string
    title: string
    // R0001 group 0135 / N0008 (시안 3): AI review signals for the trust colour + badge on the feed row.
    // null when the document has no review yet; verdict is the latest AI verdict.
    review?: {
      status: string | null
      verdict: 'pass' | 'issues' | 'hold' | null
      finding_count: number
    } | null
  } | null
  transition: { from_state: string | null; to_state: string | null } | null
  navigation: DashboardNavigation
}

export interface DashboardWorkflow {
  group_id: string
  group_title: string
  requirement: { doc_id: string; title: string }
  stage: {
    state: 'pending' | 'in_progress' | 'done'
    type_code: string
    head_doc_id: string | null
    head_doc_title: string | null
    head_doc_review_status: string | null
  }
  progress: {
    completed_steps: number
    total_steps: number
    percent: number
  }
  updated_at: string
  navigation: DashboardNavigation
}

interface DashboardList<T> {
  limit: number
  total: number
  has_more: boolean
  items: T[]
}

export interface DashboardSummary {
  ok: true
  project_id: string
  generated_at: string
  recent_activities: DashboardList<DashboardActivity>
  active_workflows: DashboardList<DashboardWorkflow>
}

export interface DashboardSummaryEntry {
  data: DashboardSummary | null
  initialLoading: boolean
  refreshing: boolean
  error: string | null
  appliedGeneratedAt: number
  requestVersion: number
  inFlight: boolean
  dirtyDuringFlight: boolean
}

const DEBOUNCE_MS = 250

function newEntry(): DashboardSummaryEntry {
  return {
    data: null,
    initialLoading: false,
    refreshing: false,
    error: null,
    appliedGeneratedAt: 0,
    requestVersion: 0,
    inFlight: false,
    dirtyDuringFlight: false,
  }
}

export const useDashboardStore = defineStore('dashboard', () => {
  const projectStore = useProjectStore()
  const entries = reactive<Record<string, DashboardSummaryEntry>>({})
  const timers = new Map<string, ReturnType<typeof setTimeout>>()
  const requests = new Map<string, Promise<void>>()

  function entryFor(projectId: string): DashboardSummaryEntry {
    entries[projectId] ??= newEntry()
    return entries[projectId]
  }

  const currentEntry = computed(() => {
    const projectId = projectStore.currentProjectId
    return projectId ? entryFor(projectId) : null
  })

  async function fetchSummary(projectId: string): Promise<void> {
    const entry = entryFor(projectId)
    if (entry.inFlight) {
      entry.dirtyDuringFlight = true
      return requests.get(projectId)
    }

    const version = ++entry.requestVersion
    entry.inFlight = true
    entry.error = null
    if (entry.data) entry.refreshing = true
    else entry.initialLoading = true

    const request = (async () => {
      try {
        // Fetch a generous window so the "view all" toggle in the overview can
        // reveal the full set without a refetch; the card previews only the
        // newest few (10 activities / 3 workflows) until the user expands.
        const response = await getRequest<DashboardSummary>(
          `/api/v1/projects/${encodeURIComponent(projectId)}/dashboard/summary`,
          { activity_limit: 50, workflow_limit: 50 },
        )
        const summary = response.data
        const generatedAt = Date.parse(summary.generated_at)
        if (
          summary.project_id === projectId
          && entry.requestVersion === version
          && Number.isFinite(generatedAt)
          && generatedAt >= entry.appliedGeneratedAt
        ) {
          entry.data = summary
          entry.appliedGeneratedAt = generatedAt
        }
      } catch (error: any) {
        if (entry.requestVersion === version) {
          entry.error = String(
            error?.response?.data?.error_message
            ?? error?.response?.data?.detail
            ?? error?.message
            ?? 'dashboard_load_failed',
          )
        }
      } finally {
        if (entry.requestVersion === version) {
          entry.inFlight = false
          entry.initialLoading = false
          entry.refreshing = false
          if (entry.dirtyDuringFlight) {
            entry.dirtyDuringFlight = false
            invalidate(projectId)
          }
        }
        requests.delete(projectId)
      }
    })()

    requests.set(projectId, request)
    return request
  }

  function invalidate(projectId: string, immediate = false): void {
    const entry = entryFor(projectId)
    const timer = timers.get(projectId)
    if (timer) {
      clearTimeout(timer)
      timers.delete(projectId)
    }
    if (entry.inFlight) {
      entry.dirtyDuringFlight = true
      return
    }
    if (immediate) {
      void fetchSummary(projectId)
      return
    }
    timers.set(projectId, setTimeout(() => {
      timers.delete(projectId)
      void fetchSummary(projectId)
    }, DEBOUNCE_MS))
  }

  function retryCurrent(): void {
    const projectId = projectStore.currentProjectId
    if (projectId) invalidate(projectId, true)
  }

  return {
    entries,
    currentEntry,
    entryFor,
    fetchSummary,
    invalidate,
    retryCurrent,
  }
})
