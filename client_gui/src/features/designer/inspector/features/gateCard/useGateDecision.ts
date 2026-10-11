// flowgate.v02.0053 T#9 — a gate card's ✓ 확인 · ✕ 반려 on the Graph run (P0008 S6-1). Reads the Flow's current
// run while a gate is the target (again on every run event of its Flow) and sends the decision for this gate.
// The answer is the run after the decision; the Canvas reads itself again on the same run events, so the card
// and the header follow. A refusal keeps its reason (the server's code) until the next decision or target.
import { computed, ref, shallowRef, watch, type ComputedRef, type Ref } from 'vue'
import { documentErrorOf, runCurrentRead, runGateDecide, type RunCurrent, type RunGateDecision } from '@/features/document'
import type { InspectorTarget } from '../../inspectorTarget'
import { useInspectorEvents, type InspectorEventMatch } from '../../useInspectorResource'
import { gateActions, gateFailureKey, type GateActionItem, type GateOperation } from './gateCardModel'

/** Run events of the target Flow after which its run is read again. */
export const GATE_RUN_EVENTS = ['graph_run_status_changed', 'graph_node_state_changed'] as const

/** A run event of any node of the target Flow (the frame carries its group_id). */
export const gateRunEventMatches: InspectorEventMatch = (frame, scope) => frame.groupId === scope.flowId

export interface GateDecisionState {
  readonly actions: ComputedRef<Readonly<Record<GateOperation, GateActionItem>>>
  /** The decision waiting for its answer, or null. */
  readonly busy: Readonly<Ref<RunGateDecision | null>>
  /** Message key (under `gui.inspector.gateCard.`) of the last refusal for this gate, or null. */
  readonly failureKey: Readonly<Ref<string | null>>
  /** Sends the decision (a reject may carry its reason). Whether it was taken. */
  decide(decision: RunGateDecision, comment?: string): Promise<boolean>
}

/** The gate behind the target (flow \n node), or null: not a gate, or a card not saved yet. */
const gateOf = (target: InspectorTarget | null) =>
  target && target.nodeType === 'gate' && !target.nodeId.startsWith('tmp_') ? `${target.flowId}\n${target.nodeId}` : null

export function useGateDecision(target: () => InspectorTarget | null): GateDecisionState {
  const run = shallowRef<RunCurrent | null>(null)
  const busy = ref<RunGateDecision | null>(null)
  const failureKey = ref<string | null>(null)
  let readSeq = 0

  function read() {
    const gate = gateOf(target())
    const token = ++readSeq
    if (gate === null) {
      run.value = null
      return
    }
    runCurrentRead(gate.split('\n')[0]).then(
      (current) => {
        if (token === readSeq && gateOf(target()) === gate) run.value = current
      },
      () => undefined,
    )
  }

  watch(
    () => gateOf(target()),
    () => {
      run.value = null
      failureKey.value = null
      read()
    },
    { immediate: true },
  )

  // The Canvas read carries the gate's node state; when it changes (the top band ran, stopped or resumed the
  // Flow, another card finished) the run is read again too — also when the event stream is not connected.
  watch(
    () => (gateOf(target()) ? `${target()?.nodeRun?.status ?? ''}\n${target()?.canvasState ?? ''}` : null),
    (now, before) => {
      if (now !== null && before !== undefined && now !== before) read()
    },
  )

  useInspectorEvents(() => (gateOf(target()) ? target() : null), {
    events: GATE_RUN_EVENTS,
    matches: gateRunEventMatches,
    changed: read,
  })

  const actions = computed(() => {
    const current = target()
    return current ? gateActions(current, run.value, busy.value !== null) : gateActions({ docId: null, docType: null, docReviewStatus: null, docStatus: null, nodeType: null, cardDocuments: [] })
  })

  async function decide(decision: RunGateDecision, comment?: string): Promise<boolean> {
    const current = target()
    const gate = gateOf(current)
    const runId = run.value?.runId
    if (!current || gate === null || !runId || !actions.value[decision].enabled) return false
    busy.value = decision
    failureKey.value = null
    let ok = false
    try {
      const answer = await runGateDecide(runId, current.nodeId, decision, comment)
      if (gateOf(target()) === gate) run.value = answer
      ok = true
    } catch (error) {
      if (gateOf(target()) === gate) failureKey.value = gateFailureKey(documentErrorOf(error))
    } finally {
      busy.value = null
    }
    if (!ok) read()
    return ok
  }

  return { actions, busy, failureKey, decide }
}
