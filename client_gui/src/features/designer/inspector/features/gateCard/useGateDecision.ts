import { computed, inject, watch } from 'vue'
import { documentErrorOf, type RunGateDecision } from '@/features/document'
import type { InspectorTarget } from '../../inspectorTarget'
import { INSPECTOR_HOST } from '../../inspectorHost'
import { gateActions, gateFailureKey, gateResult } from './gateCardModel'
import { checkControlFailure } from '@/features/document'
import { observeGateApproval } from './useGateApproval'
import { useGateContext } from './useGateContext'
export { GATE_RUN_EVENTS, gateRunEventMatches } from './useGateContext'

export function useGateDecision(target: () => InspectorTarget | null) {
  const context = useGateContext(target)
  watch(context, value => observeGateApproval(target(), value), { immediate: true })
  const host = inject(INSPECTOR_HOST, null)
  const busy = computed<RunGateDecision | null>(() => {
    const c = context.value
    return c?.busy.value ?? (c?.gate.value?.decision_in_progress && !c.gate.value.job?.last_error_code ? 'confirm' : null)
  })
  const failureKey = computed(() => {
    const c = context.value
    const error = c?.decisionError.value ?? c?.error.value ?? Object.values(c?.failures.value ?? {})[0]
    const specific = error ? checkControlFailure(error) : null
    return specific && documentErrorOf(error).code !== 'gate_git_unavailable' ? `settings:${specific.key}` : error ? gateFailureKey(documentErrorOf(error)) : null
  })
  const actions = computed(() => {
    const current = target()
    const c = context.value
    return gateActions(current ?? { docId: null, docType: null, docReviewStatus: null, docStatus: null, nodeType: null, cardDocuments: [] },
      c?.run.value ?? null, busy.value !== null || c?.status.value !== 'ready' || Object.keys(c?.pending.value ?? {}).length > 0 || Object.keys(c?.failures.value ?? {}).length > 0 || !c?.node.value?.attemptId || c?.settings.value?.settingsVersion === undefined)
  })
  const cancelAction = computed(() => {
    const c = context.value
    const current = target()
    const visible = current?.nodeType === 'gate' && c?.gate.value?.can_cancel === true && !c.gate.value.decision_in_progress
    return { visible, enabled: visible && !current?.readOnly && busy.value === null && c?.status.value === 'ready'
      && Object.keys(c.pending.value).length === 0 && !!c.node.value?.attemptId && c.settings.value?.settingsVersion !== undefined }
  })
  const attemptId = computed(() => context.value?.node.value?.attemptId ?? null)
  const result = computed(() => gateResult(context.value?.node.value ?? null))
  watch(() => context.value?.gate.value?.job?.last_error_code, error => {
    if (error) host?.request({ kind: 'show-tool', tool: 'git' })
  })
  async function decide(decision: RunGateDecision, comment?: string) {
    if (decision !== 'confirm' && decision !== 'cancel') return false
    const c = context.value
    const current = target()
    if (!(decision === 'cancel' ? cancelAction.value.enabled : actions.value.confirm.enabled) || !c || !current) return false
    const ok = await c.decide(decision, comment)
    host?.request({ kind: 'refresh-flow', flowId: current.flowId })
    if (!ok && target()?.flowId === current.flowId && target()?.nodeId === current.nodeId && c.gate.value?.decision_in_progress) host?.request({ kind: 'show-tool', tool: 'git' })
    return ok
  }
  return { actions, cancelAction, attemptId, busy, failureKey, result, decide,
    decisionHeld: computed(() => context.value?.gate.value?.decision_in_progress === true),
    settings: computed(() => context.value?.settings.value ?? null),
    receipt: computed(() => {
      const c = context.value
      const receipt = c?.gate.value?.decision
      return receipt && (c?.gate.value?.decision_in_progress || receipt.attempt_id === c?.node.value?.attemptId || receipt.attempt_id === null) ? receipt : null
    }),
    errorCode: computed(() => context.value?.gate.value?.recovery?.error_code ?? context.value?.gate.value?.job?.last_error_code ?? null),
  }
}
export type GateDecisionState = ReturnType<typeof useGateDecision>

export { checkOperationErrorKey, checkSettingsSummary, type RunGateResult, type SettingsNode } from '@/features/document'
