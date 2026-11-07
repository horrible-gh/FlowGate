import { effectScope, shallowReactive } from 'vue'
import type { InspectorTarget } from '../../inspectorTarget'
import { documentErrorOf } from '@/features/document'
import { checkControlFailure } from '../checkSettings/checkSettingsModel'
import { gateDecisionReason, gateFailureKey } from './gateCardModel'
import { useGateContext } from './useGateContext'

const acquiring = shallowReactive(new Set<string>())
type Context = NonNullable<ReturnType<typeof useGateContext>['value']>
const observed = shallowReactive(new Map<string, Context>())
export function observeGateApproval(target: InspectorTarget | null, context: Context | null) {
  if (target && context) observed.set(`${target.flowId}\n${target.nodeId}`, context)
}
/** Reads existing refs only; Canvas rendering never starts a settings/Git query. */
export function gateApprovalView(flowId: string, nodeId: string, attemptId?: string | null) {
  const key = `${flowId}\n${nodeId}`
  const context = observed.get(key)
  const busy = acquiring.has(key) || !!context?.busy.value
  if (!context || context.node.value?.attemptId !== attemptId) return { busy, blocked: busy, reason: busy ? 'gui.inspector.gateCard.reason.busy' : null }
  const failed = Object.keys(context.failures.value).length > 0
  const blocked = busy || failed || Object.keys(context.pending.value).length > 0 || context.gate.value?.decision_in_progress === true
  const error = context.decisionError.value ?? context.error.value ?? Object.values(context.failures.value)[0]
  const specific = error ? checkControlFailure(error) : null
  const reason = specific ? `gui.inspector.checkSettings.${specific.key}` : error ? `gui.inspector.gateCard.${gateFailureKey(documentErrorOf(error))}` : blocked ? 'gui.inspector.gateCard.reason.busy' : null
  return { busy, blocked, reason }
}
/** Scoped acquire/reload; it never changes selection and shares busy/request/version with every Inspector part. */
export async function approveGateTarget(target: InspectorTarget): Promise<boolean> {
  const key = `${target.flowId}\n${target.nodeId}`
  if (target.nodeType !== 'gate' || target.readOnly === 'conflict' || acquiring.has(key)) return false
  acquiring.add(key)
  const scope = effectScope(true)
  try {
    const context = scope.run(() => useGateContext(() => target))!
    const c = context.value
    observeGateApproval(target, c)
    if (!c || c.busy.value || Object.keys(c.pending.value).length || Object.keys(c.failures.value).length) return false
    if (!await c.reload() || c.status.value !== 'ready' || !c.node.value?.attemptId || c.settings.value?.settingsVersion === undefined) return false
    if (gateDecisionReason(target, c.run.value, c.busy.value !== null || c.gate.value?.decision_in_progress === true) || Object.keys(c.failures.value).length) return false
    return await c.decide('confirm')
  } finally { scope.stop(); acquiring.delete(key) }
}
