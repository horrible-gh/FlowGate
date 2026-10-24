// Gate jobs use the existing Git error/detail screen and its common actions (D0007 §6/7.3).
import { randomUuid } from '@shared/utils/uuid'
import { computed, inject, ref, shallowRef, watch, type InjectionKey } from 'vue'
import { documentErrorOf, runCurrentRead, runGateRecover, type RunCurrent, type RunGateState, type RunGateRecoveryRequest } from '@/features/document'
import { useInspectorEvents } from '../designer/inspector/useInspectorResource'
export interface GateOperation { runId: string; nodeKey: string; gate: RunGateState }
export function useGateOperations(project: () => string | null, flow: () => string | null, readOnly: () => boolean) {
  const run = shallowRef<RunCurrent | null>(null)
  const selected = shallowRef<GateOperation | null>(null)
  const busy = ref(false)
  const notice = ref<string | null>(null)
  const operations = computed<GateOperation[]>(() => (run.value?.nodes ?? []).flatMap(node => {
    const gate = node.gate
    return gate?.decision_in_progress && gate.recovery && (gate.recovery.error_code || ['failed', 'recovery_required'].includes(gate.recovery.status))
      ? [{ runId: run.value!.runId, nodeKey: node.nodeKey, gate }] : []
  }))
  let sequence = 0
  let pending: { operation: GateOperation; action: 'retry' | 'cancel' | 'resolve'; body: RunGateRecoveryRequest } | null = null
  async function reload(): Promise<boolean> {
    const group = flow()
    const seq = ++sequence
    if (!group) { run.value = null; return false }
    try {
      const current = await runCurrentRead(group)
      if (seq === sequence && group === flow()) run.value = current
      return true
    } catch (cause) {
      if (seq === sequence && documentErrorOf(cause).kind === 'forbidden') { run.value = null; selected.value = null }
      return false
    }
  }
  watch(() => `${project()}\n${flow()}`, () => {
    sequence++
    run.value = null
    selected.value = null
    notice.value = null
    pending = null
    void reload()
  }, { immediate: true })
  useInspectorEvents(() => project() && flow() ? { projectId: project()!, flowId: flow()!, docId: null } : null, {
    events: ['graph_run_status_changed', 'graph_node_state_changed', 'git_status_changed', 'gate_publish_job_changed'],
    matches: (frame, scope) => frame.groupId === scope.flowId,
    changed: () => { void reload() },
  })
  function select(operation: GateOperation) {
    // The immutable GET snapshot is exactly the evidence/version the person is reviewing.
    selected.value = operation
    notice.value = null
  }
  async function act(action: 'retry' | 'cancel' | 'resolve', resolution?: 'effects_confirmed' | 'abandon', reason?: string): Promise<boolean> {
    const operation = selected.value
    const recovery = operation?.gate.recovery
    if (!operation || !recovery || busy.value || readOnly()) return false
    if (pending && (pending.operation.gate.recovery?.decision_id !== recovery.decision_id || pending.action !== action)) return false
    if (!pending && !(action === 'retry' ? recovery.retryable : action === 'cancel' ? recovery.cancelable : recovery.resolvable && !!reason?.trim())) return false
    const group = flow()
    const request = pending ?? { operation, action, body: {
      request_id: randomUuid(), expected_decision_version: recovery.decision_version,
      ...(action === 'resolve' ? { resolution, reason: reason!.trim(), evidence_version: recovery.evidence_version, evidence_refs: [...recovery.evidence_refs] } : {}),
    } }
    pending = request
    busy.value = true
    notice.value = null
    const send = () => runGateRecover(request.operation.runId, request.operation.nodeKey, request.operation.gate.recovery!.decision_id, request.action, request.body)
    try {
      let answer: RunCurrent | null
      try { answer = await send() }
      catch (cause) {
        if (documentErrorOf(cause).kind !== 'network') throw cause
        if (!await reload()) throw cause
        answer = await send()
      }
      if (flow() === group) { run.value = answer; pending = null; selected.value = null }
      return true
    } catch (cause) {
      if (flow() === group) {
        const error = documentErrorOf(cause)
        notice.value = error.code || 'Git operation failed'
        if (error.kind !== 'network') pending = null
        await reload()
      }
      return false
    } finally { busy.value = false }
  }
  return { operations, selected, busy, notice, select, act, reload, readOnly }
}
export type GateOperations = ReturnType<typeof useGateOperations>
export const GATE_OPERATIONS_CONTEXT: InjectionKey<GateOperations> = Symbol('flowgate.gitGateOperations')
export const useGitGateOperations = () => inject(GATE_OPERATIONS_CONTEXT, null)
