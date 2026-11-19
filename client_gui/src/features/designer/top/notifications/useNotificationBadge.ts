import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { qaOpenItems } from '@/features/document'
import type { ExplorerProject } from '../../projectExplorer/projectExplorerTree'
import { subscribeProjectEvents } from '@/features/project/projectEvents'
import { fetchActiveRuns, fetchNotificationFeed } from './notificationApi'
export function useNotificationBadge(projectId: () => string | null | undefined, project: () => ExplorerProject | null | undefined, isOpen: () => boolean = () => false) {
  const unseen = ref(false)
  let acknowledgement = 0
  watch(isOpen, open => { acknowledgement++; if (open) unseen.value = false }, { flush: 'sync' })
  let generation = 0, timer: ReturnType<typeof setInterval> | undefined, unsubscribe: (() => void) | undefined
  watch(projectId, () => {
    const ticket = ++generation, pid = projectId()
    clearInterval(timer); unsubscribe?.(); unseen.value = false
    if (!pid) return
    let loading = false, queued = false
    const snapshots: (string | undefined)[] = []
    const reload = async () => {
      if (loading) { queued = true; return }
      loading = true; queued = false
      const observedAcknowledgement = acknowledgement, startedOpen = isOpen()
      const results = await Promise.allSettled([fetchActiveRuns(), qaOpenItems(pid), fetchNotificationFeed(pid)])
      loading = false
      if (ticket !== generation) return
      const flows = new Set(project()?.groups.flatMap(g => g.flows.map(f => f.id)) ?? [])
      results.forEach((result, index) => {
        // A failed read is unknown, not an empty snapshot or a new notification.
        if (result.status !== 'fulfilled') return
        let value: unknown = result.value
        if (index === 0) value = (result.value as Awaited<ReturnType<typeof fetchActiveRuns>>).filter(r => flows.has(r.groupId)).map(r => JSON.stringify(r)).sort()
        if (index === 1) value = (result.value as Awaited<ReturnType<typeof qaOpenItems>>).map(q => JSON.stringify(q)).sort()
        if (index === 2) {
          const feed = result.value as Awaited<ReturnType<typeof fetchNotificationFeed>>
          if (feed.degraded_sections?.includes('ai_runs')) return
          value = (feed.ai_invoke_runs?.items ?? []).map(r => JSON.stringify(r)).sort()
        }
        const snapshot = JSON.stringify(value)
        if (snapshots[index] !== undefined && snapshots[index] !== snapshot && !startedOpen && !isOpen() && observedAcknowledgement === acknowledgement) unseen.value = true
        snapshots[index] = snapshot
      })
      if (isOpen()) unseen.value = false
      if (queued) void reload()
    }
    unsubscribe = subscribeProjectEvents(pid, { events: ['ai_invoke_started', 'ai_invoke_finished', 'qna_q_registered', 'qna_answer_registered', 'group_view_refresh'], event: () => { void reload() }, resync: () => { void reload() } })
    timer = setInterval(() => { void reload() }, 5000); void reload()
  }, { immediate: true })
  onBeforeUnmount(() => { generation++; clearInterval(timer); unsubscribe?.() })
  return computed(() => unseen.value)
}
