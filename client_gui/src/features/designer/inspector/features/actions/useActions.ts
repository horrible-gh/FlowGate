// flowgate.v02.0048 T#6 — the header actions' state and operations (D#1 §7.2 / §7.3, §8.2). Two reads
// through useInspectorResource: the target document's review state (status, group disposed) and
// whether an AI run owns its Flow's group (`/ai-invoke/active`). Each operation calls the existing
// v0.1 API through `@/features/document`, then reads its own two resources again; a refusal (409
// state contention, 423 / 409 AI running, 403 …) is shown as a reason and the state is read again
// too (D#1 §8.1). What the change means to the rest of the Designer — the header, the Canvas card,
// the documents tab — travels as the server's own events through the hub, never by calling
// another feature (D#1 §7.3).
// flowgate.v02.0056 F2-1: approve / reject (and the TR commit line of an approval) are gone — see actionsModel.
import { computed, ref, watch, type ComputedRef, type Ref } from 'vue'
import { reviewActivityRead, reviewDocumentRead, reviewErrorOf, reviewRunStart, type ReviewDocument } from '@/features/document'
import type { InspectorTarget } from '../../inspectorTarget'
import { useInspectorResource, type InspectorEventMatch } from '../../useInspectorResource'
import {
  actionsAvailability,
  actionsFailureKey,
  actionsReviewStatus,
  type ActionsAvailability,
  type ActionsFailureKind,
  type ActionsOperation,
} from './actionsModel'

/** Server events after which the document's review state is read again (D#1 §7.4). */
export const ACTIONS_DOCUMENT_EVENTS = ['doc_review_status_changed', 'ai_review_arrived', 'document_explorer_refresh'] as const

/** Server events after which the group's AI activity is read again (D#1 §7.4). */
export const ACTIONS_ACTIVITY_EVENTS = ['ai_invoke_started', 'ai_invoke_finished'] as const

/** An AI run event of any document of the target Flow (the run's frame carries its group_id). */
export const actionsActivityMatches: InspectorEventMatch = (frame, scope) => frame.groupId === scope.flowId

export interface ActionsState {
  readonly availability: ComputedRef<ActionsAvailability>
  /** The operation waiting for its answer, or null. */
  readonly busy: Readonly<Ref<ActionsOperation | null>>
  /** Message key of the last refusal for this target, or null. */
  readonly failureKey: ComputedRef<string | null>
  run(): Promise<boolean>
  rework(): Promise<boolean>
}

export function useActions(target: () => InspectorTarget | null): ActionsState {
  const docState = useInspectorResource<ReviewDocument>({ target, read: reviewDocumentRead, events: ACTIONS_DOCUMENT_EVENTS })
  const activity = useInspectorResource({
    target,
    read: () => {
      const flowId = target()?.flowId
      return flowId ? reviewActivityRead(flowId) : Promise.reject(new Error('no target Flow'))
    },
    events: ACTIONS_ACTIVITY_EVENTS,
    matches: actionsActivityMatches,
  })

  const busy = ref<ActionsOperation | null>(null)
  const failure = ref<ActionsFailureKind | null>(null)

  // A refusal belongs to the document it was answered for.
  watch(
    () => target()?.docId ?? null,
    () => {
      failure.value = null
    },
  )

  const readDocument = computed(() => {
    const read = docState.data.value
    const current = target()
    return read && current && read.record.docId === current.docId ? read : null
  })

  const availability = computed(() => {
    const current = target()
    return actionsAvailability({
      cardKind: current?.cardKind ?? 'other',
      ownership: current?.ownership ?? null,
      readOnly: current?.readOnly ?? null,
      docReviewStatus: current ? actionsReviewStatus(current, readDocument.value?.record ?? null) : null,
      // An unanswered activity read does not block: the server refuses while a run owns the group.
      aiRunning: activity.data.value?.active === true,
      groupDisposed: readDocument.value?.groupDisposed === true,
      busy: busy.value !== null,
    })
  })

  async function perform(operation: ActionsOperation, call: (docId: string, flowId: string) => Promise<void>): Promise<boolean> {
    const current = target()
    if (!current?.docId || !availability.value[operation].enabled) return false
    const docId = current.docId
    busy.value = operation
    failure.value = null
    let ok = false
    try {
      await call(docId, current.flowId)
      ok = true
    } catch (error) {
      if (target()?.docId === docId) failure.value = reviewErrorOf(error).kind
    } finally {
      busy.value = null
    }
    // Success or refusal, the state shown is read again from the server.
    await Promise.all([docState.reload(), activity.reload()])
    return ok
  }

  return {
    availability,
    busy,
    failureKey: computed(() => (failure.value ? actionsFailureKey(failure.value) : null)),
    run: () =>
      perform('run', async (docId, flowId) => {
        await reviewRunStart({ groupId: flowId, docId, scope: 'edit' })
      }),
    rework: () =>
      perform('rework', async (docId, flowId) => {
        await reviewRunStart({ groupId: flowId, docId, scope: 'rework' })
      }),
  }
}
