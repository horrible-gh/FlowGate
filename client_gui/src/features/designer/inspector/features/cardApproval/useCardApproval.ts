// flowgate.v02.0056 F2-1 — the card approval bar's state and its one operation (O1 0005-TR §4-1). The card state
// comes from the target (inspectorCardState: the Canvas read's node run, the Canvas card's rule); [승인] reads the
// Flow's current run for its id and approves the card with the result the screen shows. After an approval — and
// after a refusal that means the card moved (result_changed, already_approved, node_not_in_review, no_result,
// node_not_started, document_approval_refused …) — the Flow's Canvas is read again (host intent `refresh-flow`),
// which reads the run too: the card, its document lines and the run band follow the server. A busy refusal
// (group_ai_running, card_approval_busy, ai_running) keeps the button and is told in a toast by the bar.
import { computed, inject, ref, shallowRef, watch, type ComputedRef, type Ref } from 'vue'
import { runCardApprovalErrorOf, runCardApprove, runCurrentRead, type RunCardApprovalError } from '@/features/document'
import { INSPECTOR_HOST } from '../../inspectorHost'
import { inspectorApprovalFailure, inspectorCardNodeKey, inspectorCardState, type CardStateView, type InspectorTarget } from '../../inspectorTarget'
import { cardApprovalBar, type CardApprovalBarView, type CardApprovalFailure } from './cardApprovalModel'

export interface CardApprovalState {
  /** The card state (작업 중 · 승인 대기 · 승인됨 …), or null (no Graph card with a run). */
  readonly state: ComputedRef<CardStateView | null>
  readonly bar: ComputedRef<CardApprovalBarView>
  /** [승인] is being answered. */
  readonly busy: Readonly<Ref<boolean>>
  /** The last refusal for this card, or null. */
  readonly failure: Readonly<Ref<CardApprovalFailure | null>>
  /** Grows with every refusal, so the same refusal twice is still told (the toast). */
  readonly failureSeq: Readonly<Ref<number>>
  /** The card was approved here (until another card is the target). */
  readonly approved: Readonly<Ref<boolean>>
  /** Approves the card. Whether it was approved. */
  approve(): Promise<boolean>
}

function failureOf(error: RunCardApprovalError): CardApprovalFailure {
  return {
    view: inspectorApprovalFailure(error.code, error.status, error.network),
    code: error.code,
    message: error.message,
    docId: error.docId,
    reason: error.reason,
    approvedDocIds: error.approvedDocIds,
  }
}

/** No running or stopped run holds the card any more. */
const NO_RUN: RunCardApprovalError = {
  code: 'run_not_active',
  status: 409,
  network: false,
  message: '',
  docId: null,
  reason: null,
  detail: null,
  approvedDocIds: [],
  resultDocId: null,
}

export function useCardApproval(target: () => InspectorTarget | null): CardApprovalState {
  const host = inject(INSPECTOR_HOST, null)
  const busy = ref(false)
  const failure = shallowRef<CardApprovalFailure | null>(null)
  const failureSeq = ref(0)
  const approved = ref(false)

  /** The card behind the target (flow \n node), or null. */
  const cardOf = (current: InspectorTarget | null) => {
    const nodeKey = inspectorCardNodeKey(current)
    return current && nodeKey ? `${current.flowId}\n${nodeKey}` : null
  }

  // A refusal or an approval belongs to the card it was answered for.
  watch(
    () => cardOf(target()),
    () => {
      failure.value = null
      approved.value = false
    },
  )

  const state = computed(() => inspectorCardState(target()))
  const bar = computed(() =>
    cardApprovalBar({ state: state.value, readOnly: target()?.readOnly ?? null, busy: busy.value, canApprove: target()?.canApprove === true }),
  )

  async function approve(): Promise<boolean> {
    const current = target()
    const card = cardOf(current)
    const nodeKey = inspectorCardNodeKey(current)
    if (!current || card === null || nodeKey === null || !bar.value.approveVisible || !bar.value.enabled) return false
    // The result the screen shows: the server refuses with result_changed when the node's result moved meanwhile.
    const resultDocId = current.nodeRun?.resultDocId ?? null
    busy.value = true
    failure.value = null
    approved.value = false
    let ok = false
    let refused: CardApprovalFailure | null = null
    try {
      const run = await runCurrentRead(current.flowId)
      if (!run) refused = failureOf(NO_RUN)
      else {
        await runCardApprove(run.runId, nodeKey, resultDocId)
        ok = true
      }
    } catch (error) {
      refused = failureOf(runCardApprovalErrorOf(error))
    } finally {
      busy.value = false
    }
    if (cardOf(target()) === card) {
      approved.value = ok
      failure.value = refused
      if (refused) failureSeq.value += 1
    }
    // The Canvas (with it the card, its document lines and the run band) is read again from the server.
    if (ok || refused?.view.reread) host?.request({ kind: 'refresh-flow', flowId: current.flowId })
    return ok
  }

  return { state, bar, busy, failure, failureSeq, approved, approve }
}
