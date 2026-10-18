// flowgate.v02.0053 T#3 — the open document windows of this tab. One window per document: opening a
// document that already has a window brings that window back (restored, in front) instead of a second
// one, and an edit request turns it to its editor. New windows cascade so several stay visible.
// Module state, not a store: the windows are screen state of this tab only and are never sent anywhere.
// A window follows its document's supersede chain (T#3 rework 2): a run result that replaced the card's
// empty report becomes what the window shows (`shown`). A window is found by either id, and two windows
// never show the same document (followDocumentWindow).
// flowgate.v02.0056 F1 — the review history windows (0056 CH §2) are a second list kept the same way: one
// 검수·반려 이력 window per document, opened from the history button of a document line, cascaded apart
// from the document windows so both stay visible.
// flowgate.v02.0070 T#4 (B4) — an exact window (`exact`): the document of that very id, as history — it never follows
// the supersede chain and is read only (no 편집 / 본문 다시 입력 / 복구). The 「전환 전 문서」 link opens the document an
// R↔B succession came from this way, so the old id is shown instead of the new document it was succeeded by. An exact
// window is a window of its own (`id` = `exact:<docId>`): a usual open of the same document opens the usual window, and
// an exact open never brings back a usual one. Its opener may put a part under the body (`aside`, e.g. the old
// document's Q&A history).
import { markRaw, reactive, type Component } from 'vue'
import { closeAllDocumentAiWindows } from './documentAiWindows'
import { closeAllSlotWriteWindows } from './slotWriteWindows'

export type DocumentWindowMode = 'view' | 'edit'

/** flowgate.v02.0070 T#4 — a part an exact window draws under its body, with the props it is given. */
export interface DocumentWindowAside {
  readonly component: Component
  readonly props?: Readonly<Record<string, unknown>>
}

/** flowgate.v02.0070 T#4 — how a document window opens. */
export interface DocumentWindowOpenOptions {
  /** That very document, as history: no supersede chain, read only (no edit / re-enter / recover). */
  readonly exact?: boolean
  /** An exact window's part under the body (ignored on a usual window). */
  readonly aside?: DocumentWindowAside | null
}

export interface DocumentWindowState {
  /** The window's identity: `docId`, or `exact:<docId>` for an exact window. */
  readonly id: string
  /** The document the window was opened for. */
  readonly docId: string
  /** The document it shows and edits: `docId`, or the end of its supersede chain (never followed when `exact`). */
  shown: string
  /** flowgate.v02.0070 T#4: shows exactly `docId`, read only. */
  readonly exact: boolean
  /** The exact window's part under the body, or null. */
  readonly aside: DocumentWindowAside | null
  mode: DocumentWindowMode
  minimized: boolean
  readonly x: number
  readonly y: number
  /** Raised on every request for this window; the window brings itself to the front when it changes. */
  raise: number
}

/** A document's review history window (flowgate.v02.0056 F1). */
export interface DocumentHistoryWindowState {
  readonly docId: string
  minimized: boolean
  readonly x: number
  readonly y: number
  /** Raised on every request for this window; the window brings itself to the front and reads again. */
  raise: number
}

const CASCADE_STEPS = 6
const CASCADE_PX = 28
const ORIGIN_X = 200
const ORIGIN_Y = 96
/** The history windows start to the right of the document windows' origin. */
const HISTORY_ORIGIN_X = 260
const HISTORY_ORIGIN_Y = 132

const windows = reactive<DocumentWindowState[]>([])
const historyWindows = reactive<DocumentHistoryWindowState[]>([])
let cascade = 0
let historyCascade = 0

/** The open windows, oldest first (reactive). */
export function documentWindowList(): readonly DocumentWindowState[] {
  return windows
}

/** The usual window opened for a document, else the usual window showing it, or undefined (exact windows are not). */
export function documentWindowOf(docId: string): DocumentWindowState | undefined {
  return windows.find((win) => !win.exact && win.docId === docId) ?? windows.find((win) => !win.exact && win.shown === docId)
}

const exactId = (docId: string) => `exact:${docId}`

/** The window of that identity (`docId` for a usual window, DocumentWindowState.id). */
const windowById = (id: string) => windows.find((win) => win.id === id)

function bringBack(win: DocumentWindowState, mode: DocumentWindowMode): void {
  win.minimized = false
  if (mode === 'edit') win.mode = 'edit'
  win.raise += 1
}

/**
 * Opens a document's window: `view` shows the document, `edit` goes straight to its editor. A window
 * already open for the document is restored and brought to the front; `edit` turns it to the editor,
 * `view` leaves an editor that is open as it is (its unsaved text stays).
 * flowgate.v02.0070 T#4: `exact` opens (or brings back) the document's exact window instead — always the view.
 */
export function openDocumentWindow(docId: string, mode: DocumentWindowMode = 'view', options: DocumentWindowOpenOptions = {}): void {
  if (!docId) return
  const exact = options.exact === true
  const open = exact ? windowById(exactId(docId)) : documentWindowOf(docId)
  if (open) {
    bringBack(open, exact ? 'view' : mode)
    return
  }
  const offset = (cascade++ % CASCADE_STEPS) * CASCADE_PX
  const aside = exact && options.aside ? { ...options.aside, component: markRaw(options.aside.component) } : null
  windows.push({
    id: exact ? exactId(docId) : docId,
    docId,
    shown: docId,
    exact,
    aside,
    mode: exact ? 'view' : mode,
    minimized: false,
    x: ORIGIN_X + offset,
    y: ORIGIN_Y + offset,
    raise: 0,
  })
}

/**
 * The window opened for `docId` now shows `shown` (the end of its supersede chain). When another window
 * already has that document, this one closes and the other is brought back instead (in `mode`: an edit
 * turns it to its editor, as a second open request would). Returns whether this window stays.
 */
export function followDocumentWindow(docId: string, shown: string, mode: DocumentWindowMode = 'view'): boolean {
  const win = windowById(docId)
  if (!win || win.exact) return false
  if (win.shown === shown) return true
  const other = windows.find((item) => item !== win && !item.exact && (item.docId === shown || item.shown === shown))
  if (!other) {
    win.shown = shown
    return true
  }
  closeDocumentWindow(docId)
  bringBack(other, mode)
  return false
}

/** `id`: the window's identity (a document id for its usual window); an exact window stays in its view. */
export function setDocumentWindowMode(id: string, mode: DocumentWindowMode): void {
  const open = windowById(id)
  if (open && !open.exact) open.mode = mode
}

export function setDocumentWindowMinimized(id: string, minimized: boolean): void {
  const open = windowById(id)
  if (open) open.minimized = minimized
}

/** Closes a window by its identity (the window asks about unsaved text before it calls this). */
export function closeDocumentWindow(id: string): void {
  const index = windows.findIndex((win) => win.id === id)
  if (index !== -1) windows.splice(index, 1)
}

// --- Review history windows (flowgate.v02.0056 F1) -------------------------------------------------

/** The open history windows, oldest first (reactive). */
export function documentHistoryWindowList(): readonly DocumentHistoryWindowState[] {
  return historyWindows
}

export function documentHistoryWindowOf(docId: string): DocumentHistoryWindowState | undefined {
  return historyWindows.find((win) => win.docId === docId)
}

/**
 * Opens a document's review history window. A window already open for the document is restored, brought
 * to the front and reads its history again.
 */
export function openDocumentHistoryWindow(docId: string): void {
  if (!docId) return
  const open = documentHistoryWindowOf(docId)
  if (open) {
    open.minimized = false
    open.raise += 1
    return
  }
  const offset = (historyCascade++ % CASCADE_STEPS) * CASCADE_PX
  historyWindows.push({ docId, minimized: false, x: HISTORY_ORIGIN_X + offset, y: HISTORY_ORIGIN_Y + offset, raise: 0 })
}

export function setDocumentHistoryWindowMinimized(docId: string, minimized: boolean): void {
  const open = documentHistoryWindowOf(docId)
  if (open) open.minimized = minimized
}

export function closeDocumentHistoryWindow(docId: string): void {
  const index = historyWindows.findIndex((win) => win.docId === docId)
  if (index !== -1) historyWindows.splice(index, 1)
}

/**
 * Closes every window — document and history, and (flowgate.v02.0056 F2-2) every [작성] and AI call window with
 * the written lines and the followed runs (sign-out, tests).
 */
export function closeAllDocumentWindows(): void {
  windows.splice(0, windows.length)
  historyWindows.splice(0, historyWindows.length)
  cascade = 0
  historyCascade = 0
  closeAllSlotWriteWindows()
  closeAllDocumentAiWindows()
}
