import { computed, inject, watch } from 'vue'
import { documentErrorOf, type RunGateDecision } from '@/features/document'
import type { InspectorTarget } from '../../inspectorTarget'
import { INSPECTOR_HOST } from '../../inspectorHost'
import { gateActions, gateFailureKey, gateResult } from './gateCardModel'
import { checkControlFailure } from '../checkSettings/checkSettingsModel'
import { useGateContext } from './useGateContext'
export { GATE_RUN_EVENTS, gateRunEventMatches } from './useGateContext'

export function useGateDecision(target: () => InspectorTarget | null) {
  const context = useGateContext(target)
  const host = inject(INSPECTOR_HOST, null)
  const busy = computed<RunGateDecision | null>(() => {
    const c = context.value
    return c?.busy.value ?? (c?.gate.value?.decision_in_progress && !c.gate.value.job?.last_error_code ? 'confirm' : null)
  })
  const failureKey = computed(() => {
    const error = context.value?.decisionError.value ?? context.value?.error.value
    const specific = error ? checkControlFailure(error) : null
    return specific && documentErrorOf(error).code !== 'gate_git_unavailable' ? `settings:${specific.key}` : error ? gateFailureKey(documentErrorOf(error)) : null
  })
  const actions = computed(() => {
    const current = target()
    const c = context.value
    return gateActions(current ?? { docId: null, docType: null, docReviewStatus: null, docStatus: null, nodeType: null, cardDocuments: [] },
      c?.run.value ?? null, busy.value !== null || c?.status.value !== 'ready' || Object.keys(c?.pending.value ?? {}).length > 0 || !c?.node.value?.attemptId || c?.settings.value?.settingsVersion === undefined)
  })
  const result = computed(() => gateResult(context.value?.node.value ?? null))
  watch(() => context.value?.gate.value?.job?.last_error_code, error => {
    if (error) host?.request({ kind: 'show-tool', tool: 'git' })
  })
  async function decide(decision: RunGateDecision, comment?: string) {
    const c = context.value
    const current = target()
    if (!actions.value[decision].enabled || !c || !current) return false
    const ok = await c.decide(decision, comment)
    host?.request({ kind: 'refresh-flow', flowId: current.flowId })
    if (!ok && target()?.flowId === current.flowId && target()?.nodeId === current.nodeId && c.gate.value?.decision_in_progress) host?.request({ kind: 'show-tool', tool: 'git' })
    return ok
  }
  return { actions, busy, failureKey, result, decide,
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
