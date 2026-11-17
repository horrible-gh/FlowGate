import { computed, getCurrentInstance, onScopeDispose, reactive, watch } from 'vue'
import { getRequest } from '@shared/api'
import { useProjectStore } from '@/features/project'
import { finishedRunFailed } from './runAssessment'

// Local admission and server execution are separate: cancelling is still active until confirmed.
const pending = reactive<Record<string, number>>({})
export function beginChatActivity(groupId: string): () => void {
  pending[groupId] = (pending[groupId] ?? 0) + 1
  return () => { pending[groupId] = Math.max(0, (pending[groupId] ?? 1) - 1) }
}

/**
 * flowgate.v02test.0035: `onFinish` hears every chat (CH) run of an open Flow that the server reports finished, and
 * whether it failed (finishedRunFailed), before the activity it ends is read again.
 */
export function useFlowChatActivity(project: () => string | null, flows: () => readonly string[],
  onFinish?: (groupId: string, failed: boolean) => void) {
  const active = reactive<Record<string, boolean>>({})
  const tickets = new Map<string, number>()
  const pinia = getCurrentInstance()?.appContext.config.globalProperties.$pinia
  const store = pinia ? useProjectStore(pinia) : null
  let disposed = false
  const reading = new Set<string>(), again = new Set<string>()
  async function refresh(id: string) {
    if (reading.has(id)) { again.add(id); return }
    reading.add(id)
    const pid = project(), ticket = (tickets.get(id) ?? 0) + 1
    tickets.set(id, ticket)
    try {
      const { data } = await getRequest<{ chat_runs?: unknown[]; active?: boolean; doc_ref?: string }>(
        '/api/v1/ai-invoke/active', { group_id: id })
      if (disposed || project() !== pid || tickets.get(id) !== ticket || !flows().includes(id)) return
      active[id] = Array.isArray(data.chat_runs) ? data.chat_runs.length > 0
        : data.active === true && typeof data.doc_ref === 'string' && data.doc_ref.endsWith('-CH')
    } catch { /* A failed read is not evidence that a chat finished. */ }
    finally {
      reading.delete(id)
      if (again.delete(id) && !disposed && flows().includes(id)) void refresh(id)
    }
  }
  const refreshAll = () => { for (const id of flows()) void refresh(id) }
  watch(project, (pid, _, cleanup) => {
    tickets.clear()
    for (const key of Object.keys(active)) delete active[key]
    if (!pid || !store) return
    cleanup(store.subscribeProjectEvents(pid, {
      events: ['ai_invoke_started', 'ai_invoke_finished'],
      event: frame => {
        const id = frame.groupId ?? String(frame.payload.group_id ?? '')
        if (!flows().includes(id)) return
        if (frame.type === 'ai_invoke_finished' && onFinish) {
          const p = frame.payload as { doc_ref?: unknown; action_scope?: unknown; end_reason?: unknown; exit_code?: unknown }
          if ((typeof p.doc_ref === 'string' && p.doc_ref.endsWith('-CH')) || p.action_scope === 'chat') {
            onFinish(id, finishedRunFailed(typeof p.end_reason === 'string' ? p.end_reason : null, p.exit_code))
          }
        }
        // Re-read all live CH runs: an old finish must never clear a newer run.
        void refresh(id)
      },
      resync: refreshAll,
    }))
    refreshAll()
  }, { immediate: true })
  watch(() => [...flows()], refreshAll, { immediate: true })
  watch(() => ({ ...pending }), refreshAll)
  if (typeof window !== 'undefined') window.addEventListener('online', refreshAll)
  onScopeDispose(() => {
    disposed = true
    if (typeof window !== 'undefined') window.removeEventListener('online', refreshAll)
  })
  return computed(() => Object.fromEntries(flows().map(id => [id, active[id] === true || (pending[id] ?? 0) > 0])))
}
