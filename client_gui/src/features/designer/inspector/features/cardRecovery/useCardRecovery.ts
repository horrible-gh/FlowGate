// 0083 WP-2: use the existing run API and Inspector refresh intent; never auto-resume.
import { computed, inject, ref, shallowRef, watch } from 'vue'
import { runCardRecover, runCurrentRead, runCardApprovalErrorOf, type RunCurrent } from '@/features/document'
import { INSPECTOR_HOST } from '../../inspectorHost'
import { inspectorCardNodeKey, type InspectorTarget } from '../../inspectorTarget'

export function useCardRecovery(target: () => InspectorTarget | null) {
  const host = inject(INSPECTOR_HOST, null)
  const run = shallowRef<RunCurrent | null>(null)
  const busy = ref(false)
  const done = ref(false)
  const failure = ref<string | null>(null)
  let generation = 0
  const candidate = computed(() => {
    const current = target()
    return !!current && inspectorCardNodeKey(current) !== null && !current.rootBound
      && current.nodeRun?.status === 'open' && !current.nodeRun.resultDocId
      && current.ownership === 'graph' && current.readOnly !== 'conflict' && !current.editLock
  })
  watch(() => [target(), candidate.value], async () => {
    const own = ++generation
    run.value = null
    failure.value = null
    done.value = false
    const current = target()
    if (!current || !candidate.value) return
    try {
      const answer = await runCurrentRead(current.flowId)
      if (generation === own) run.value = answer
    } catch {
      // An unknown/failed read is not evidence of idle; keep recovery disabled.
    }
  }, { immediate: true })
  const enabled = computed(() => candidate.value && !busy.value && !done.value
    && run.value?.status === 'idle' && run.value.idleReason !== 'stopped'
    && run.value.nodes.some(n => n.nodeKey === target()?.nodeId && n.status === 'open' && !n.resultDocId))

  async function recover() {
    const current = target()
    const runId = run.value?.runId
    if (!current || !runId || !enabled.value) return false
    const own = generation
    busy.value = true
    failure.value = null
    let ok = false
    try {
      const answer = await runCardRecover(runId, current.nodeId)
      ok = true
      if (generation === own) { run.value = answer; done.value = true }
    } catch (error) {
      if (generation === own) {
        const refused = runCardApprovalErrorOf(error)
        failure.value = refused.message || ''
      }
    } finally {
      busy.value = false
    }
    host?.request({ kind: 'refresh-flow', flowId: current.flowId })
    return ok
  }
  return { candidate, enabled, busy, done, failure, recover }
}
