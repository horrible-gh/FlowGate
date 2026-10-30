import { onScopeDispose, ref, shallowRef, watch } from 'vue'
import { getAiInvocations } from '@/features/project/aiInvocationsApi'
import type { AiInvocationsPage } from '@/features/project'
import { inspectorCardNodeKey, type InspectorTarget } from '../../inspectorTarget'
import { useInspectorEvents } from '../../useInspectorResource'
export function useAiInvocations(target: () => InspectorTarget | null) {
  const page = shallowRef<AiInvocationsPage | null>(null)
  const loading = ref(false), error = ref<string | null>(null)
  let epoch = 0
  const key = () => { const current = target(); const node = inspectorCardNodeKey(current); return current && node ? `${current.projectId}\n${current.flowId}\n${node}` : null }
  async function read(more = false) {
    const current = target(), ownKey = key()
    if (!current || !ownKey || (more && (loading.value || !page.value?.nextCursor || !page.value.available))) return
    const cursor = more ? page.value?.nextCursor ?? null : null
    const token = ++epoch; loading.value = true; error.value = null
    try {
      const answer = await getAiInvocations(current.flowId, inspectorCardNodeKey(current)!, cursor)
      if (token !== epoch || ownKey !== key()) return
      page.value = more && page.value && answer.available ? { ...answer, invocations: [...page.value.invocations, ...answer.invocations] } : answer
    } catch (e) { if (token === epoch && ownKey === key()) error.value = e instanceof Error ? e.message : String(e) }
    finally { if (token === epoch) loading.value = false }
  }
  watch(key, () => { ++epoch; page.value = null; error.value = null; loading.value = false; void read() }, { immediate: true, flush: 'sync' })
  useInspectorEvents(target, { events: ['ai_invoke_started', 'ai_invoke_finished'], changed: () => { void read() } })
  onScopeDispose(() => { ++epoch })
  return { page, loading, error, reload: () => read(), next: () => read(true) }
}
