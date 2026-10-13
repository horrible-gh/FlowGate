// flowgate.v02.0056 (0017-CH) — the open card memo windows (shared by every part of the feature, not per component). The header [메모] opens a new memo and the documents tab
// list opens a written one; both reach the same windows, drawn by the header's CardMemoWindows (the header stays
// while a card is selected, whichever tab is shown). A window keeps the card it was opened for.
// Like a document line, a memo line opens its memo to read (열기) or straight in its form (✎); a new memo is a form.
import { reactive } from 'vue'
import { cardMemoDraftOf, type CardMemoFacts, type CardMemoFailure } from './cardMemoModel'
import { sameCard, type CardMemoCard } from './useCardMemo'

export interface CardMemoWindow {
  readonly id: string
  readonly card: CardMemoCard
  /** null until a new memo is saved. */
  memoId: string | null
  /** The memo as last answered: what a change is measured against and the header shows. */
  memo: CardMemoFacts | null
  draft: { title: string; body: string; aiRef: boolean }
  /** The form is shown (a new memo, ✎, or [편집] in the window); else the memo is read. */
  editing: boolean
  busy: boolean
  failure: CardMemoFailure | null
  confirming: boolean
  /** The memo is gone (deleted elsewhere). */
  gone: boolean
  readonly x: number
  readonly y: number
}

export const cardMemoWindows = reactive<CardMemoWindow[]>([])

let opened = 0

function placed(): { x: number; y: number } {
  const offset = (opened++ % 6) * 28
  return { x: 320 + offset, y: 140 + offset }
}

/**
 * Opens a memo's window (a written one once; null: a new memo of the card). `edit`: straight in its form — a window
 * already open to read turns to its form.
 */
export function openCardMemoWindow(card: CardMemoCard, memo: CardMemoFacts | null, edit = false): void {
  const shown = memo ? cardMemoWindows.find((win) => win.memoId === memo.memoId && sameCard(win.card, card)) : undefined
  if (shown) {
    if (edit) editCardMemoWindow(shown)
    return
  }
  const id = memo ? `memo:${memo.memoId}:${opened}` : `new:${opened}`
  cardMemoWindows.push({
    id,
    card,
    memoId: memo?.memoId ?? null,
    memo,
    draft: { ...cardMemoDraftOf(memo) },
    editing: memo === null || edit,
    busy: false,
    failure: null,
    confirming: false,
    gone: false,
    ...placed(),
  })
}

/** A window read so far turns to its form, starting from the memo as last answered. */
export function editCardMemoWindow(win: CardMemoWindow): void {
  if (win.editing) return
  win.draft = { ...cardMemoDraftOf(win.memo) }
  win.editing = true
}

/** The windows of a memo deleted from its line close (a memo already gone has nothing to show). */
export function closeCardMemoWindowsOf(card: CardMemoCard, memoId: string): void {
  for (const win of cardMemoWindows.filter((item) => item.memoId === memoId && sameCard(item.card, card))) closeCardMemoWindow(win)
}

export function closeCardMemoWindow(win: CardMemoWindow): void {
  const index = cardMemoWindows.indexOf(win)
  if (index !== -1) cardMemoWindows.splice(index, 1)
}

/** Closes every window (a test starts clean). */
export function closeAllCardMemoWindows(): void {
  cardMemoWindows.splice(0)
}
