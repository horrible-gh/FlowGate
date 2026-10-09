// flowgate.v02.0048 T#7 — the Q&A tab's state and its tab number (D#1 §7.2 / §7.3, §10.3, §10.4).
// The questions are those of the selected Node's document (`target.docId`): a task instruction Node
// shows the instruction's questions, its report Node the report's (D#1 §10.3). They are read through
// useInspectorResource and read again when the server registers a question or an answer on that
// document (`qna_q_registered` / `qna_answer_registered`, frames carrying the document's doc_id).
// Answering calls the existing v0.1 API, then reads its own resources again — success or refusal
// (D#1 §7.3, §8.1). Whether answering is possible also reads the Flow's AI activity and the group's
// disposal (`group_disposed` of the document read), the facts the server refuses on.
import { computed, reactive, ref, watch, type ComputedRef, type Ref } from 'vue'
import {
  qaAnswer,
  qaErrorOf,
  qaRead,
  reviewActivityRead,
  reviewDocumentRead,
  type QaThread,
} from '@/features/document'
import type { InspectorTarget } from '../../inspectorTarget'
import { useInspectorResource, type InspectorEventMatch, type InspectorResourceStatus } from '../../useInspectorResource'
import {
  qnaAnswerReady,
  qnaAvailability,
  qnaErrorKey,
  qnaFailureKey,
  qnaTabCount,
  qnaView,
  type QnaAvailability,
  type QnaItemView,
} from './qnaModel'

/** Server events after which the questions are read again (D#1 §7.4). */
export const QNA_EVENTS = ['qna_q_registered', 'qna_answer_registered'] as const

/** Server events after which the answering guard (AI activity, disposal) is read again. */
export const QNA_GUARD_EVENTS = ['ai_invoke_started', 'ai_invoke_finished', 'document_explorer_refresh'] as const

/** A guard event of any document of the target Flow, or of the target document itself. */
export const qnaGuardMatches: InspectorEventMatch = (frame, scope) => frame.groupId === scope.flowId || (scope.docId !== null && frame.docId === scope.docId)

interface QnaGuard {
  readonly aiRunning: boolean
  readonly groupDisposed: boolean
}

export interface QnaFailure {
  readonly itemId: number
  readonly key: string
}

export interface QnaState {
  readonly items: ComputedRef<readonly QnaItemView[] | null>
  readonly availability: ComputedRef<QnaAvailability>
  readonly status: Readonly<Ref<InspectorResourceStatus>>
  readonly stale: Readonly<Ref<boolean>>
  /** Message key of the failed read, or null. */
  readonly errorKey: ComputedRef<string | null>
  /** The question whose answer is waiting for the server or for the read after it, or null. */
  readonly busy: Readonly<Ref<number | null>>
  /** The last refused answer of this document, or null. */
  readonly failure: Readonly<Ref<QnaFailure | null>>
  /** An answer to the question could be sent as it is. */
  ready(itemId: number, body: string, picked: readonly string[]): boolean
  /** Answers one question (prose trimmed); nothing is sent when it is not ready or not enabled. */
  answer(itemId: number, body: string, picked: readonly string[]): Promise<boolean>
  reload(): Promise<void>
}

export function useQna(target: () => InspectorTarget | null): QnaState {
  const thread = useInspectorResource<QaThread>({ target, read: qaRead, events: QNA_EVENTS })
  const guard = useInspectorResource<QnaGuard>({
    target,
    read: async (docId) => {
      const flowId = target()?.flowId
      if (!flowId) throw new Error('no target Flow')
      const [activity, document] = await Promise.all([reviewActivityRead(flowId), reviewDocumentRead(docId)])
      return { aiRunning: activity.active, groupDisposed: document.groupDisposed }
    },
    events: QNA_GUARD_EVENTS,
    matches: qnaGuardMatches,
  })

  const busy = ref<number | null>(null)
  const failure = ref<QnaFailure | null>(null)
  // Questions of this document answered from here. The server adds an answer on every POST, so such a
  // question is not sent again even while the questions shown are still a read from before the answer
  // (the read after it superseded, failed or not back yet).
  const sent = reactive(new Set<number>())

  // A refusal and the questions answered belong to the document they were answered for.
  watch(
    () => target()?.docId ?? null,
    () => {
      failure.value = null
      sent.clear()
    },
  )

  const readThread = computed(() => {
    const read = thread.data.value
    const current = target()
    return read && current && read.docId === current.docId ? read : null
  })

  const items = computed(() => (readThread.value ? qnaView(readThread.value.items) : null))

  const availability = computed(() =>
    qnaAvailability({
      readOnly: target()?.readOnly ?? null,
      // An unanswered guard read does not block: the server refuses on the same facts.
      groupDisposed: guard.data.value?.groupDisposed === true,
      aiRunning: guard.data.value?.aiRunning === true,
      busy: busy.value !== null,
    }),
  )

  const errorKey = computed(() => (thread.status.value === 'error' ? qnaErrorKey(qaErrorOf(thread.error.value).kind) : null))

  function ready(itemId: number, body: string, picked: readonly string[]): boolean {
    const item = readThread.value?.items.find((candidate) => candidate.id === itemId)
    return !!item && !sent.has(itemId) && qnaAnswerReady(item, body, picked)
  }

  async function answer(itemId: number, body: string, picked: readonly string[]): Promise<boolean> {
    const current = target()
    if (!current?.docId || !availability.value.answer.enabled || !ready(itemId, body, picked)) return false
    const docId = current.docId
    busy.value = itemId
    failure.value = null
    let ok = false
    try {
      try {
        await qaAnswer({ docId, itemId, body: body.trim(), selectedOptionIds: picked })
        ok = true
        if (target()?.docId === docId) sent.add(itemId)
      } catch (error) {
        if (target()?.docId === docId) failure.value = { itemId, key: qnaFailureKey(qaErrorOf(error).kind) }
      }
      // Success or refusal, the questions and the guard are read again from the server; answering
      // stays closed until that read is back, so the old form cannot send the same answer twice.
      await Promise.all([thread.reload(), guard.reload()])
    } finally {
      busy.value = null
    }
    return ok
  }

  return {
    items,
    availability,
    status: thread.status,
    stale: thread.stale,
    errorKey,
    busy,
    failure,
    ready,
    answer,
    reload: async () => {
      await Promise.all([thread.reload(), guard.reload()])
    },
  }
}

/**
 * The Q&A tab number supplier (D#1 §5.2, §10.4): the questions of the target document waiting for
 * the user, read on its own and again on the Q&A events. None while there is no target, before the
 * first read succeeds, or when nothing waits; a failed read again keeps the last known number.
 */
export function useQnaCount(target: Readonly<Ref<InspectorTarget | null>>): Readonly<Ref<number | null>> {
  const thread = useInspectorResource<QaThread>({ target: () => target.value, read: qaRead, events: QNA_EVENTS })
  return computed(() => {
    const read = thread.data.value
    const current = target.value
    return read && current && read.docId === current.docId ? qnaTabCount(read.items) : null
  })
}
