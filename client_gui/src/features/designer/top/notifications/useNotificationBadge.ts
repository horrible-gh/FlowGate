import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { qaOpenItems } from '@/features/document/api/qa'
import type { ExplorerProject } from '../../projectExplorer/projectExplorerTree'
import { subscribeProjectEvents } from '@/features/project/projectEvents'
import { fetchActiveRuns } from './notificationApi'
import { notificationCounts, countLabel } from './notificationCounts'
export function useNotificationBadge(projectId: () => string | null | undefined, project: () => ExplorerProject | null | undefined) {
  const count = ref(0)
  let generation = 0, timer: ReturnType<typeof setInterval> | undefined, unsubscribe: (() => void) | undefined
  watch([projectId, project], () => {
    const ticket = ++generation, pid = projectId()
    clearInterval(timer); unsubscribe?.(); count.value = 0
    if (!pid) return
    let loading = false
    const reload = async () => {
      if (loading) return
      loading = true
      const [runs, questions] = await Promise.allSettled([fetchActiveRuns(), qaOpenItems(pid)])
      loading = false
      if (ticket !== generation) return
      const flows = new Set(project()?.groups.flatMap(g => g.flows.map(f => f.id)) ?? [])
      count.value = notificationCounts(runs.status === 'fulfilled' ? runs.value.filter(r => flows.has(r.groupId)) : [], questions.status === 'fulfilled' ? questions.value : null).bell
    }
    unsubscribe = subscribeProjectEvents(pid, { events: ['ai_invoke_started', 'ai_invoke_finished', 'qna_q_registered', 'qna_answer_registered', 'group_view_refresh'], event: () => { void reload() }, resync: () => { void reload() } })
    timer = setInterval(() => { void reload() }, 5000); void reload()
  }, { immediate: true })
  onBeforeUnmount(() => { generation++; clearInterval(timer); unsubscribe?.() })
  return computed(() => countLabel(count.value))
}
