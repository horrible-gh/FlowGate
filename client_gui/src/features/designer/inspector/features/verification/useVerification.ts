// flowgate.v02test.0053 1-D — the card's `검증` line state. The tab's visibility supplier (one per Inspector Frame) owns the
// reading: on selection, again soon while the line is shown, at the moment a running card's line would appear, otherwise
// slowly; 403 / 404 hide the line and stop asking. The answers are shared per card, so the line part (mounted only while
// the line is shown) draws the same answer and its actions write back into it. A button always names the exact run the
// line was drawn for; a refused action reads the line again (409 selfcheck_not_waiting: the request has just started).
import { computed, getCurrentScope, inject, onScopeDispose, reactive, ref, watch, type ComputedRef, type Ref } from 'vue'
import {
  cardVerificationAct,
  cardVerificationErrorOf,
  cardVerificationRead,
  type CardVerification,
  type CardVerificationAction,
} from '@/features/document'
import { INSPECTOR_HOST } from '../../inspectorHost'
import { inspectorCardNodeKey, type InspectorTarget } from '../../inspectorTarget'
import {
  BUTTON_ACTION,
  CONFIRMED_BUTTONS,
  OPEN_CARD_EVENT,
  VERIFICATION_FEEDBACK_MS,
  VERIFICATION_IDLE_POLL_MS,
  nextReadDelay,
  verificationRowView,
  type VerificationButton,
  type VerificationRowView,
} from './verificationModel'

/** The last answer per card (`flowId \0 nodeKey`). */
const answers = reactive(new Map<string, CardVerification>())
/** The reader watching each card: read now, or show an answer and read again after a given time. */
const readers = new Map<string, Reader>()

interface Reader {
  reload(): Promise<void>
  /** An action's answer: shown now, read again after `delay` (or when the answer says). */
  take(next: CardVerification, delay?: number): void
}

export const verificationCardKey = (target: InspectorTarget | null): string | null => {
  const nodeKey = inspectorCardNodeKey(target)
  return target && nodeKey ? `${target.flowId}\u0000${nodeKey}` : null
}

/** Test support: forget every shared answer. */
export function resetVerificationAnswers(): void {
  answers.clear()
  readers.clear()
}

/**
 * The documents tab visibility supplier: true while the server says the card's line is shown. Reads while the
 * registration applies to the selected card (the target is null otherwise) and for as long as the caller's scope lives.
 */
export function useVerificationVisible(target: Readonly<Ref<InspectorTarget | null>>): Readonly<Ref<boolean>> {
  let currentKey: string | null = null
  let ticket = 0
  let timer: ReturnType<typeof setTimeout> | undefined

  const schedule = (delay: number) => {
    clearTimeout(timer)
    timer = setTimeout(() => void reload(), delay)
  }

  async function reload(): Promise<void> {
    clearTimeout(timer)
    const current = target.value
    const key = verificationCardKey(current)
    if (!current || !key || key !== currentKey) return
    const mine = ++ticket
    try {
      const next = await cardVerificationRead(current.flowId, current.nodeId)
      if (mine !== ticket || key !== currentKey) return
      answers.set(key, next)
      schedule(nextReadDelay(next, Date.now()))
    } catch (thrown) {
      if (mine !== ticket || key !== currentKey) return
      const failure = cardVerificationErrorOf(thrown)
      // No write permission, or no stored card: nothing to show and nothing to ask again.
      if (failure.status === 403 || failure.status === 404) {
        answers.delete(key)
        return
      }
      schedule(VERIFICATION_IDLE_POLL_MS)
    }
  }

  const reader: Reader = {
    reload,
    take(next, delay) {
      if (!currentKey) return
      ++ticket
      answers.set(currentKey, next)
      schedule(delay ?? nextReadDelay(next, Date.now()))
    },
  }

  function release(): void {
    clearTimeout(timer)
    ++ticket
    if (currentKey && readers.get(currentKey) === reader) readers.delete(currentKey)
  }

  watch(
    () => {
      const current = target.value
      return [verificationCardKey(current), current?.nodeRun?.status ?? null, current?.nodeRun?.attemptId ?? null].join('|')
    },
    () => {
      const key = verificationCardKey(target.value)
      if (key !== currentKey) {
        release()
        if (currentKey) answers.delete(currentKey)
        currentKey = key
        if (key) readers.set(key, reader)
      }
      void reload()
    },
    { immediate: true },
  )

  if (getCurrentScope()) {
    onScopeDispose(() => {
      release()
      if (currentKey) answers.delete(currentKey)
      currentKey = null
    })
  }

  return computed(() => {
    const key = verificationCardKey(target.value)
    return !!key && !!answers.get(key)?.visible
  })
}

/** The card ahead can be shown from here: the same Flow, or a Flow of the open project. */
export function canGoTo(target: InspectorTarget | null, verification: CardVerification | null): boolean {
  const blocker = verification?.blocker
  if (!target || !blocker) return false
  return blocker.flowId === target.flowId || blocker.projectId === target.projectId
}

export interface VerificationRowState {
  readonly verification: ComputedRef<CardVerification | null>
  readonly row: ComputedRef<VerificationRowView | null>
  /** The button whose [확인] [취소] is shown in its place, or null. */
  readonly confirming: Readonly<Ref<VerificationButton | null>>
  readonly busy: Readonly<Ref<boolean>>
  press(button: VerificationButton): Promise<void>
  confirm(): Promise<void>
  dismiss(): void
}

/** The line part: the shared answer of its card and the line's buttons. */
export function useVerificationRow(target: () => InspectorTarget | null): VerificationRowState {
  const host = inject(INSPECTOR_HOST, null)
  const confirming = ref<VerificationButton | null>(null)
  const busy = ref(false)
  const verification = computed(() => {
    const key = verificationCardKey(target())
    return key ? answers.get(key) ?? null : null
  })

  // A confirmation belongs to the run (and the line) it was asked for.
  watch(() => [verificationCardKey(target()), verification.value?.state, verification.value?.run?.runId].join('|'), () => {
    confirming.value = null
  })

  async function act(action: CardVerificationAction): Promise<void> {
    const current = target()
    const key = verificationCardKey(current)
    const runId = verification.value?.run?.runId
    confirming.value = null
    if (!current || !key || !runId || busy.value) return
    busy.value = true
    try {
      const next = await cardVerificationAct(current.flowId, current.nodeId, runId, action)
      const reader = readers.get(key)
      // Ended / 취소됨 stay briefly; the next read hides them.
      const settled = next.state === 'stopped' || next.state === 'cancelled'
      if (reader) reader.take(next, settled ? VERIFICATION_FEEDBACK_MS : undefined)
      else answers.set(key, next)
    } catch {
      busy.value = false
      await readers.get(key)?.reload()
    } finally {
      busy.value = false
    }
  }

  function go(): void {
    const current = target()
    const blocker = verification.value?.blocker
    if (!current || !blocker || !canGoTo(current, verification.value)) return
    if (blocker.flowId === current.flowId) {
      host?.request({ kind: 'select-node', nodeId: blocker.nodeKey })
      return
    }
    window.dispatchEvent(new CustomEvent(OPEN_CARD_EVENT, {
      detail: { projectId: blocker.projectId ?? current.projectId, flowId: blocker.flowId, nodeKey: blocker.nodeKey },
    }))
  }

  async function press(button: VerificationButton): Promise<void> {
    if (button === 'go') return go()
    if (busy.value) return
    if (CONFIRMED_BUTTONS.includes(button)) {
      confirming.value = button
      return
    }
    await act(BUTTON_ACTION[button])
  }

  async function confirm(): Promise<void> {
    const button = confirming.value
    if (!button || button === 'go') return
    await act(BUTTON_ACTION[button])
  }

  const row = computed(() => verificationRowView(verification.value, canGoTo(target(), verification.value)))

  return {
    verification,
    row,
    confirming,
    busy,
    press,
    confirm,
    dismiss: () => {
      confirming.value = null
    },
  }
}
