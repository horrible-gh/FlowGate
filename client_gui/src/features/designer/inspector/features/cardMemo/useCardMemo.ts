// flowgate.v02.0056 F2-2 — the card memos of the selected card and their writes (O3 0009-TR §4-5). The list is read
// when a stored card is the target (a `tmp_` card and a Node without a definition have none). No server event
// announces a memo change, so every write follows its own answer at once and reads the list again.
// A write keeps the card it was made for: a window may outlive the selection of its card.
import { computed, ref, shallowRef, watch, type ComputedRef, type Ref } from 'vue'
import {
  cardMemoCreate,
  cardMemoDelete,
  cardMemoErrorOf,
  cardMemoList,
  cardMemoUpdate,
  type CardMemo,
  type CardMemoInput,
} from '@/features/document'
import { inspectorCardNodeKey, type InspectorTarget } from '../../inspectorTarget'
import { cardMemoFailure, cardMemoLockedReason, type CardMemoFailure } from './cardMemoModel'

/** A stored card: its Flow and node key. */
export interface CardMemoCard {
  readonly flowId: string
  readonly nodeKey: string
}

export type CardMemoWrite<T> = { readonly ok: true; readonly value: T } | { readonly ok: false; readonly failure: CardMemoFailure }

export interface CardMemoState {
  /** The selected stored card, or null (no memos). */
  readonly card: ComputedRef<CardMemoCard | null>
  /** The card's memos in written order. */
  readonly memos: Readonly<Ref<readonly CardMemo[]>>
  readonly status: Readonly<Ref<'idle' | 'loading' | 'ready' | 'error'>>
  /** The read failure (shown with a retry), or null. */
  readonly readFailure: Readonly<Ref<CardMemoFailure | null>>
  /** Why the memos cannot be changed here (a message key), or null. */
  readonly lockedReason: ComputedRef<string | null>
  reload(): Promise<void>
  create(card: CardMemoCard, input: CardMemoInput): Promise<CardMemoWrite<CardMemo>>
  update(card: CardMemoCard, memoId: string, input: CardMemoInput): Promise<CardMemoWrite<CardMemo>>
  remove(card: CardMemoCard, memoId: string): Promise<CardMemoWrite<string>>
}

const sameCard = (a: CardMemoCard | null, b: CardMemoCard | null) => !!a && !!b && a.flowId === b.flowId && a.nodeKey === b.nodeKey

export function useCardMemo(target: () => InspectorTarget | null): CardMemoState {
  const card = computed<CardMemoCard | null>(() => {
    const current = target()
    const nodeKey = inspectorCardNodeKey(current)
    return current && nodeKey ? { flowId: current.flowId, nodeKey } : null
  })
  const memos = shallowRef<readonly CardMemo[]>([])
  const status = ref<'idle' | 'loading' | 'ready' | 'error'>('idle')
  const readFailure = shallowRef<CardMemoFailure | null>(null)
  const lockedReason = computed(() => cardMemoLockedReason(target()))
  // Request order: only the latest read may set the list.
  let generation = 0

  async function reload(): Promise<void> {
    const current = card.value
    const seq = ++generation
    if (!current) {
      memos.value = []
      status.value = 'idle'
      readFailure.value = null
      return
    }
    if (status.value !== 'ready') status.value = 'loading'
    try {
      const read = await cardMemoList(current.flowId, current.nodeKey)
      if (seq !== generation) return
      memos.value = read
      status.value = 'ready'
      readFailure.value = null
    } catch (error) {
      if (seq !== generation) return
      readFailure.value = cardMemoFailure(cardMemoErrorOf(error))
      // A failed read again keeps the shown list; the first one shows the reason.
      if (status.value !== 'ready') status.value = 'error'
    }
  }

  watch(
    () => (card.value ? `${card.value.flowId}\n${card.value.nodeKey}` : ''),
    () => {
      memos.value = []
      status.value = 'idle'
      readFailure.value = null
      void reload()
    },
    { immediate: true },
  )

  /** After a write: the answer is shown at once on the card it was for, and the list is read again. */
  function follow(written: CardMemoCard, change: (list: readonly CardMemo[]) => readonly CardMemo[]): void {
    if (!sameCard(written, card.value)) return
    memos.value = change(memos.value)
    void reload()
  }

  function refused(written: CardMemoCard, error: unknown): CardMemoWrite<never> {
    const failure = cardMemoFailure(cardMemoErrorOf(error))
    if (failure.reread && sameCard(written, card.value)) void reload()
    return { ok: false, failure }
  }

  async function create(written: CardMemoCard, input: CardMemoInput): Promise<CardMemoWrite<CardMemo>> {
    try {
      const memo = await cardMemoCreate(written.flowId, written.nodeKey, input)
      follow(written, (list) => [...list.filter((item) => item.memoId !== memo.memoId), memo])
      return { ok: true, value: memo }
    } catch (error) {
      return refused(written, error)
    }
  }

  async function update(written: CardMemoCard, memoId: string, input: CardMemoInput): Promise<CardMemoWrite<CardMemo>> {
    try {
      const memo = await cardMemoUpdate(written.flowId, written.nodeKey, memoId, input)
      follow(written, (list) => list.map((item) => (item.memoId === memo.memoId ? memo : item)))
      return { ok: true, value: memo }
    } catch (error) {
      return refused(written, error)
    }
  }

  async function remove(written: CardMemoCard, memoId: string): Promise<CardMemoWrite<string>> {
    try {
      await cardMemoDelete(written.flowId, written.nodeKey, memoId)
      follow(written, (list) => list.filter((item) => item.memoId !== memoId))
      return { ok: true, value: memoId }
    } catch (error) {
      return refused(written, error)
    }
  }

  return { card, memos, status, readFailure, lockedReason, reload, create, update, remove }
}
