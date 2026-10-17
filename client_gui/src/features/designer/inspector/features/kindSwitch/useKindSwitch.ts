// flowgate.v02.0048 T#10 — the R↔B kind switch's state and call (D#1 §7.2 / §7.3, §8.2, D#2 §3.4).
// Two reads through useInspectorResource, as the header actions do: the target document's state
// (group disposed) and whether an AI run owns its Flow's group (`/ai-invoke/active`), each read
// again on its own hub events. The switch calls the existing v0.1 converter through
// `@/features/document`.
//
// The converter gives the document a new doc_id and sends no event (D#2 §3.1), so after a switch
// the Flow's Canvas is read again through the Host intent `refresh-flow` (D#1 §5.7). The Center
// session then rebuilds the selection: the Node stays selected when its node_key survives, and the
// selection is cleared when it does not — on an unassigned Flow the root is a free document Node
// `doc:<doc_id>`, so the old key is gone (D#1 §10.2, D#2 §3.4). The Inspector target, its header,
// tabs and this feature all follow that recomputed selection; no other feature is called.
// A refusal is shown as a reason; the switch's own state is read again, and the Canvas too when the
// refusal says the Flow moved under it (kindSwitchRefreshesFlow).
//
// The answer may come after another Flow tab was activated, so `refresh-flow` names the Flow the
// switch was made in (its `flowId`): that Flow is read again at once even when its tab is not the
// active one, and no other Flow's Canvas or selection is touched (rev2).
//
// flowgate.v02.0064 T#4 (P0006 §3.1 / §6.1): the document state is read with the server's judgement
// (kindSwitchPreviewRead): `preview` tells the confirmation a number swap from a succession (the new or
// expected number, the child count); a refused judgement disables the switch with its reason; no judgement
// yet blocks nothing. The link to the document a succeeded root came from is features/kindSwitchOrigin.
import { computed, inject, ref, shallowRef, watch, type ComputedRef, type Ref } from 'vue'
import {
  kindSwitchConvert,
  kindSwitchErrorOf,
  kindSwitchPreviewRead,
  kindSwitchRefusalKind,
  reviewActivityRead,
  type KindSwitchPreview,
  type KindSwitchPreviewJudgement,
  type KindSwitchResult,
} from '@/features/document'
import { INSPECTOR_HOST } from '../../inspectorHost'
import type { InspectorTarget } from '../../inspectorTarget'
import { useInspectorResource, type InspectorEventMatch } from '../../useInspectorResource'
import {
  kindSwitchAvailability,
  kindSwitchFailureKey,
  kindSwitchRefreshesFlow,
  kindSwitchTarget,
  type KindSwitchAvailability,
  type KindSwitchFailureKind,
  type KindSwitchKind,
} from './kindSwitchModel'

/** Server events after which the document's state (group disposed) is read again (D#1 §7.4). */
export const KIND_SWITCH_DOCUMENT_EVENTS = ['doc_review_status_changed', 'document_explorer_refresh'] as const

/** Server events after which the group's AI activity is read again (D#1 §7.4). */
export const KIND_SWITCH_ACTIVITY_EVENTS = ['ai_invoke_started', 'ai_invoke_finished'] as const

/** An AI run event of any document of the target Flow (the run's frame carries its group_id). */
export const kindSwitchActivityMatches: InspectorEventMatch = (frame, scope) => frame.groupId === scope.flowId

export interface KindSwitchState {
  readonly availability: ComputedRef<KindSwitchAvailability>
  /** The kind the target switches to, or null. */
  readonly to: ComputedRef<KindSwitchKind | null>
  /** A switch is waiting for its answer (or for the state read after a refusal). */
  readonly busy: Readonly<Ref<boolean>>
  /** Message key of the last refusal for this target, or null. */
  readonly failureKey: ComputedRef<string | null>
  /** The last switch of this target that succeeded, until the Canvas moves the selection. */
  readonly done: Readonly<Ref<KindSwitchResult | null>>
  /** The server's judgement of the switch as last read, or null (not read yet / not judged). */
  readonly preview: ComputedRef<KindSwitchPreviewJudgement | null>
  /** Switches the target root to the other kind; true when the server switched it. */
  run(): Promise<boolean>
  /** Forgets the last refusal (e.g. the confirmation opens again). */
  clearFailure(): void
}

export function useKindSwitch(target: () => InspectorTarget | null): KindSwitchState {
  const host = inject(INSPECTOR_HOST, null)
  const docState = useInspectorResource<KindSwitchPreview>({ target, read: kindSwitchPreviewRead, events: KIND_SWITCH_DOCUMENT_EVENTS })
  const activity = useInspectorResource({
    target,
    read: () => {
      const flowId = target()?.flowId
      return flowId ? reviewActivityRead(flowId) : Promise.reject(new Error('no target Flow'))
    },
    events: KIND_SWITCH_ACTIVITY_EVENTS,
    matches: kindSwitchActivityMatches,
  })

  const busy = ref(false)
  const failure = ref<KindSwitchFailureKind | null>(null)
  const done = shallowRef<KindSwitchResult | null>(null)

  /** Reads the Flow the switch was made in again, whichever Flow tab is active now. */
  const readFlow = (flowId: string) => host?.request({ kind: 'refresh-flow', flowId })

  // A refusal or a finished switch belongs to the Node (and document kind) it was answered for.
  watch(
    () => `${target()?.nodeId ?? ''}\n${target()?.docId ?? ''}\n${target()?.docType ?? ''}`,
    () => {
      failure.value = null
      done.value = null
    },
  )

  const readDocument = computed(() => {
    const read = docState.data.value
    const current = target()
    return read && current && read.record.docId === current.docId ? read : null
  })

  /** The judgement for the kind the target switches to (an answer for the other kind is not used). */
  const preview = computed<KindSwitchPreviewJudgement | null>(() => {
    const judged = readDocument.value?.conversion ?? null
    const next = kindSwitchTarget(target()?.docType)
    return judged && (judged.targetType === null || judged.targetType === next) ? judged : null
  })

  const availability = computed(() => {
    const current = target()
    const judged = preview.value
    return kindSwitchAvailability({
      cardKind: current?.cardKind ?? 'other',
      docType: current?.docType ?? null,
      hasDocument: !!current?.docId,
      ownership: current?.ownership ?? null,
      readOnly: current?.readOnly ?? null,
      // An unanswered guard read does not block: the server refuses in the same states.
      aiRunning: activity.data.value?.active === true,
      groupDisposed: readDocument.value?.groupDisposed === true,
      // A finished switch waits for the Canvas read it asked for: the target still names the old id.
      busy: busy.value || done.value !== null,
      refusal: judged?.decision === 'refused' && judged.refusal ? kindSwitchRefusalKind(judged.refusal.code) : null,
    })
  })

  const to = computed(() => kindSwitchTarget(target()?.docType))

  async function run(): Promise<boolean> {
    const current = target()
    const next = to.value
    if (!current?.docId || next === null || !availability.value.switch.enabled) return false
    const docId = current.docId
    const nodeId = current.nodeId
    const flowId = current.flowId
    const sameNode = () => target()?.nodeId === nodeId && target()?.docId === docId
    busy.value = true
    failure.value = null
    done.value = null
    try {
      const result = await kindSwitchConvert(docId, next)
      if (sameNode()) done.value = result
      // The document has a new identity and no event tells the Canvas: read the Flow again.
      readFlow(flowId)
      return true
    } catch (error) {
      const kind = kindSwitchErrorOf(error).kind
      if (sameNode()) failure.value = kind
      if (kindSwitchRefreshesFlow(kind)) readFlow(flowId)
      await Promise.all([docState.reload(), activity.reload()])
      return false
    } finally {
      busy.value = false
    }
  }

  return {
    availability,
    to,
    busy,
    failureKey: computed(() => (failure.value ? kindSwitchFailureKey(failure.value) : null)),
    done,
    preview,
    run,
    clearFailure: () => {
      failure.value = null
    },
  }
}
