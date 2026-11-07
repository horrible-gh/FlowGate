// flowgate.v02.0150 — the open Q&A ↗ windows of this tab, apart from the Q&A tab that opened them. A window keeps the
// target it was opened for (its project, Flow and document), so a selection change, a tab change, a click on the
// Canvas or a focus leaving the window never closes it or moves it to another document: only its own × does. The
// Frame's window host (QnaWindowHost) draws them, one group per document; the selected target, when it is a group's
// document, refreshes the group's target (its read-only verdict and card lines).
import { shallowRef } from 'vue'
import type { InspectorTarget } from '../../inspectorTarget'

export interface QnaWindow {
  /** The window's key: its document and question. */
  readonly id: string
  readonly itemId: number
  readonly x: number
  readonly y: number
}

export interface QnaWindowDocument {
  /** projectId · flowId · docId of the target. */
  readonly key: string
  /** The target the windows were opened for (the latest selected target of the same document). */
  readonly target: InspectorTarget
  readonly windows: readonly QnaWindow[]
}

/** Every document with an open window, in opening order. Replaced on each change (never changed in place). */
export const qnaWindowDocuments = shallowRef<readonly QnaWindowDocument[]>([])

/** The window group of a target: its document's key, or null (a target without a document has no questions). */
export function qnaWindowKey(target: Pick<InspectorTarget, 'projectId' | 'flowId' | 'docId'> | null): string | null {
  return target?.docId ? [target.projectId, target.flowId, target.docId].join('\n') : null
}

let cascade = 0

/** Where the next window opens: cascaded by 28px, six places, as v7 openWin. */
export function nextQnaWindowPlace(): { x: number; y: number } {
  const offset = (cascade++ % 6) * 28
  return { x: 160 + offset, y: 140 + offset }
}

/** Opens a question's window for the target's document; a window already open stays where it is. */
export function openQnaWindow(target: InspectorTarget, itemId: number): void {
  const key = qnaWindowKey(target)
  if (!key) return
  const documents = qnaWindowDocuments.value
  const shown = documents.find((entry) => entry.key === key)
  if (shown?.windows.some((win) => win.itemId === itemId)) return
  const win: QnaWindow = { id: `${key}\n${itemId}`, itemId, ...nextQnaWindowPlace() }
  qnaWindowDocuments.value = shown
    ? documents.map((entry) => (entry === shown ? { ...entry, windows: [...entry.windows, win] } : entry))
    : [...documents, { key, target, windows: [win] }]
}

/** Closes one window (its × / 닫기); the document's group goes with its last window. */
export function closeQnaWindow(key: string, itemId: number): void {
  qnaWindowDocuments.value = qnaWindowDocuments.value.flatMap((entry) => {
    if (entry.key !== key) return [entry]
    const windows = entry.windows.filter((win) => win.itemId !== itemId)
    return windows.length ? [{ ...entry, windows }] : []
  })
}

/** The selected target of a document with open windows becomes their target (the same document, read again). */
export function followQnaWindowTarget(target: InspectorTarget | null): void {
  const key = qnaWindowKey(target)
  if (!key || !target) return
  const documents = qnaWindowDocuments.value
  const shown = documents.find((entry) => entry.key === key)
  if (!shown || shown.target === target) return
  qnaWindowDocuments.value = documents.map((entry) => (entry === shown ? { ...entry, target } : entry))
}

/** Closes every window (a test starts clean). */
export function closeAllQnaWindows(): void {
  qnaWindowDocuments.value = []
  cascade = 0
}
