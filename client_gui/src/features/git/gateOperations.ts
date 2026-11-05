// Gate jobs use the existing Git error/detail screen and its common actions (D0007 §6/7.3).
import { randomUuid } from '@shared/utils/uuid'
import { computed, inject, ref, shallowRef, watch, type InjectionKey } from 'vue'
import { documentErrorOf, runCurrentRead, runGateReceiptsRead, runGateRecover, type RunCurrent, type RunGateState, type RunGateRecoveryRequest } from '@/features/document'
import { checkOperationErrorKey } from '../designer/inspector/features/checkSettings/checkSettingsModel'
import { useInspectorEvents } from '../designer/inspector/useInspectorResource'
export interface GateOperation { runId: string; nodeKey: string; gate: RunGateState }
/**
 * flowgate.v02.0132 T#4: how an accepted retry (202) ended once the decision failed again: `same` = the same error code
 * as before the retry ("수락됐지만 같은 이유로 다시 실패"), `changed` = another one. `code` is the new error code.
 */
export interface RetryOutcome { readonly kind: 'same' | 'changed'; readonly code: string | null }
/**
 * The short text of an accepted retry that failed again (the cause after a colon, its message when it has one):
 * "수락됐지만 같은 이유로 다시 실패" / "…다른 이유로 실패".
 */
export function retryOutcomeText(outcome: RetryOutcome | null, t: (key: string) => string): string | null {
  if (!outcome) return null
  const key = checkOperationErrorKey(outcome.code)
  const cause = key ? t(`gui.inspector.checkSettings.${key}`) : outcome.code ? t('gui.inspector.checkSettings.target.operation_unknown') : null
  return `${t(outcome.kind === 'same' ? 'gui.git.gate_job.retry_same' : 'gui.git.gate_job.retry_changed')}${cause ? ` · ${cause}` : ''}`
}
/**
 * flowgate.v02.0132 T#4 (review): whether a held decision is parked again — the server's work on it ended in a failure
 * and waits for a person (decision `failed` / `recovery_required`, its job `recovery_required`, no Git work going on).
 * The accepted retry (202) itself is none of it: the decision is `accepted` with its version raised, the job `pending`
 * (`git_active`) and the error code still the earlier job's (`recovery.flags` falls back to the job's code); a transient
 * failure of the retry (job `retry_wait`, the code of that attempt) is no end either.
 */
export function parkedAgain(gate: RunGateState): boolean {
  const recovery = gate.recovery
  if (!recovery || !['failed', 'recovery_required'].includes(recovery.status)) return false
  if (gate.git_active === true || gate.parked === false) return false
  return !gate.job || gate.job.status === 'recovery_required'
}

export function useGateOperations(project: () => string | null, flow: () => string | null, readOnly: () => boolean) {
  const run = shallowRef<RunCurrent | null>(null)
  const selected = shallowRef<GateOperation | null>(null)
  const busy = ref(false)
  const notice = ref<string | null>(null)
  /** Accepted retries by decision id: the decision version and error code they were sent at (see `retryOutcomeOf`). */
  const retried = ref<Record<string, { version: number; code: string | null }>>({})
  const operations = computed<GateOperation[]>(() => (run.value?.nodes ?? []).flatMap(node => {
    const gate = node.gate
    return gate?.decision_in_progress && gate.recovery && (gate.recovery.error_code || ['failed', 'recovery_required'].includes(gate.recovery.status))
      ? [{ runId: run.value!.runId, nodeKey: node.nodeKey, gate }] : []
  }))
  /** An accepted retry matters until its decision leaves the run's held decisions (it succeeded or was closed). */
  function pruneRetried() {
    const held = new Set((run.value?.nodes ?? []).flatMap(node => node.gate?.recovery ? [node.gate.recovery.decision_id] : []))
    const kept = Object.entries(retried.value).filter(([id]) => held.has(id))
    if (kept.length !== Object.keys(retried.value).length) retried.value = Object.fromEntries(kept)
  }
  /**
   * The decision failed again after its accepted retry: the same error code as at the retry or another one. Null when no
   * retry of it was accepted, or the work it started has not ended in a new failure yet — see `parkedAgain`.
   */
  function retryOutcomeOf(operation: GateOperation | null | undefined): RetryOutcome | null {
    const recovery = operation?.gate.recovery
    const sent = recovery ? retried.value[recovery.decision_id] : undefined
    if (!operation || !recovery || !sent || recovery.decision_version <= sent.version || !recovery.error_code) return null
    if (!parkedAgain(operation.gate)) return null
    return { kind: recovery.error_code === sent.code ? 'same' : 'changed', code: recovery.error_code }
  }
  let sequence = 0
  let pending: { operation: GateOperation; action: 'retry' | 'cancel' | 'resolve'; body: RunGateRecoveryRequest } | null = null
  async function reload(): Promise<boolean> {
    const group = flow()
    const seq = ++sequence
    if (!group) { run.value = null; return false }
    try {
      const current = await runGateReceiptsRead(await runCurrentRead(group))
      if (seq === sequence && group === flow()) { run.value = current; pruneRetried() }
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
    retried.value = {}
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
      if (flow() === group) {
        run.value = answer; pending = null; selected.value = null
        // 202: the retry is accepted, the work goes on in the background; remember what it was sent at to tell its end.
        if (request.action === 'retry') retried.value = { ...retried.value, [recovery.decision_id]: { version: recovery.decision_version, code: recovery.error_code } }
        pruneRetried()
      }
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
  return { operations, selected, busy, notice, select, act, reload, readOnly, retryOutcomeOf }
}
export type GateOperations = ReturnType<typeof useGateOperations>
export const GATE_OPERATIONS_CONTEXT: InjectionKey<GateOperations> = Symbol('flowgate.gitGateOperations')
export const useGitGateOperations = () => inject(GATE_OPERATIONS_CONTEXT, null)
