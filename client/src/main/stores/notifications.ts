import { ref } from 'vue'
import { defineStore } from 'pinia'
import { getRequest, postRequest } from '@shared/api'
import type { DashboardActivity } from './dashboard'

// 🔔 Notification center store (R0001 group 0045 / NR0003 option A + option D).
//
// The feed is the persistent document-inflow history read from the server (workflow_events, via
// GET /projects/{id}/notifications) — NOT a client-side accumulation of toasts — so it survives
// reloads, tabs, and disconnects (the gap NR0003 identified). The server also returns the unread
// count derived from a per-user last-seen watermark; "mark all read" (opening the panel) POSTs the
// watermark forward. SSE inflow events only trigger a refetch (DashboardView), keeping the server
// the single source of truth for the badge so live and persistent counts never drift.
export interface OpenQuestionNotification {
  doc_id: string
  title: string | null
  type_code: string | null
}

export interface AiInvokeNotification {
  run_id: string
  doc_ref: string | null
  doc_title: string | null
  doc_type_code: string | null
  succeeded: boolean
  outcome: string | null
  docs_reached: number | null
  docs_target: number | null
  end_reason: string | null
  stop_code: string | null
  provider_name: string | null
  finished_at: string
  last_message_excerpt: string | null
}

export interface AiInvokeDetail {
  run_id: string
  doc_ref: string | null
  doc_title: string | null
  succeeded: boolean
  outcome: string | null
  end_reason: string | null
  stop_code: string | null
  stop_reason: string | null
  provider_name: string | null
  finished_at: string | null
  last_message: string | null
}

export interface NotificationFeed {
  ok: true
  project_id: string
  generated_at: string
  last_seen_at: string | null
  unread_count: number
  badge_count: number
  recent_activities: {
    limit: number
    total: number
    has_more: boolean
    items: DashboardActivity[]
  }
  ai_invoke_runs: {
    limit: number
    total: number
    has_more: boolean
    items: AiInvokeNotification[]
  }
  open_questions: {
    limit: number
    total: number
    has_more: boolean
    items: OpenQuestionNotification[]
  }
  degraded_sections: string[]
}

export const useNotificationsStore = defineStore('notifications', () => {
  const items = ref<DashboardActivity[]>([])
  const aiItems = ref<AiInvokeNotification[]>([])
  const qaItems = ref<OpenQuestionNotification[]>([])
  const qaTotal = ref(0)
  const degradedSections = ref<string[]>([])
  const unreadCount = ref(0)
  const lastSeenAt = ref<string | null>(null)
  const loading = ref(false)
  const error = ref<string | null>(null)
  const loadedProjectId = ref<string | null>(null)

  let requestVersion = 0

  async function fetchFeed(projectId: string): Promise<void> {
    if (!projectId) return
    const version = ++requestVersion
    loading.value = true
    error.value = null
    try {
      const response = await getRequest<NotificationFeed>(
        `/api/v1/projects/${encodeURIComponent(projectId)}/notifications`,
        { limit: 50 },
      )
      // Ignore a response superseded by a newer fetch (project switch / rapid SSE bursts).
      if (version !== requestVersion) return
      const data = response.data
      items.value = data.recent_activities?.items ?? []
      aiItems.value = data.ai_invoke_runs?.items ?? []
      qaItems.value = data.open_questions?.items ?? []
      qaTotal.value = data.open_questions?.total ?? 0
      degradedSections.value = data.degraded_sections ?? []
      unreadCount.value = data.badge_count ?? data.unread_count ?? 0
      lastSeenAt.value = data.last_seen_at ?? null
      loadedProjectId.value = projectId
    } catch (err: any) {
      if (version !== requestVersion) return
      error.value = String(
        err?.response?.data?.error_message ?? err?.message ?? 'notifications_load_failed',
      )
    } finally {
      if (version === requestVersion) loading.value = false
    }
  }

  // Mark the feed read up to now. Clears the badge optimistically and persists the watermark; the
  // returned timestamp becomes the highlight cutoff so already-read items stop showing as "new".
  async function markSeen(projectId: string): Promise<void> {
    if (!projectId) return
    unreadCount.value = qaTotal.value
    try {
      const response = await postRequest<{ ok: boolean; last_seen_at: string }>(
        `/api/v1/projects/${encodeURIComponent(projectId)}/notifications/seen`,
        {},
      )
      if (response.data?.last_seen_at) lastSeenAt.value = response.data.last_seen_at
    } catch {
      /* best-effort — the badge is already cleared this session; next fetch reconciles */
    }
  }

  // An item is "unread" (highlighted) when it arrived after the last-seen watermark. With no
  // watermark yet (never opened the panel) every item is new.
  function isUnread(item: DashboardActivity): boolean {
    if (!lastSeenAt.value) return true
    const seen = Date.parse(lastSeenAt.value)
    const occurred = Date.parse(item.occurred_at)
    if (!Number.isFinite(seen) || !Number.isFinite(occurred)) return false
    return occurred > seen
  }

  function reset(): void {
    requestVersion++
    items.value = []
    aiItems.value = []
    qaItems.value = []
    qaTotal.value = 0
    degradedSections.value = []
    unreadCount.value = 0
    lastSeenAt.value = null
    error.value = null
    loadedProjectId.value = null
  }

  return {
    items, aiItems, qaItems, qaTotal, degradedSections, unreadCount, lastSeenAt, loading, error,
    loadedProjectId, fetchFeed, markSeen, isUnread, reset,
  }
})


