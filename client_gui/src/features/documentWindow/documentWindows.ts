// flowgate.v02.0053 T#3 — the open document windows of this tab. One window per document: opening a
// document that already has a window brings that window back (restored, in front) instead of a second
// one, and an edit request turns it to its editor. New windows cascade so several stay visible.
// Module state, not a store: the windows are screen state of this tab only and are never sent anywhere.
// A window follows its document's supersede chain (T#3 rework 2): a run result that replaced the card's
// empty report becomes what the window shows (`shown`). A window is found by either id, and two windows
// never show the same document (followDocumentWindow).
import { reactive } from 'vue'

export type DocumentWindowMode = 'view' | 'edit'

export interface DocumentWindowState {
  /** The document the window was opened for (its identity). */
  readonly docId: string
  /** The document it shows and edits: `docId`, or the end of its supersede chain. */
  shown: string
  mode: DocumentWindowMode
  minimized: boolean
  readonly x: number
  readonly y: number
  /** Raised on every request for this window; the window brings itself to the front when it changes. */
  raise: number
}

const CASCADE_STEPS = 6
const CASCADE_PX = 28
const ORIGIN_X = 200
const ORIGIN_Y = 96

const windows = reactive<DocumentWindowState[]>([])
let cascade = 0

/** The open windows, oldest first (reactive). */
export function documentWindowList(): readonly DocumentWindowState[] {
  return windows
}

/** The window opened for a document, else the window showing it, or undefined. */
export function documentWindowOf(docId: string): DocumentWindowState | undefined {
  return windows.find((win) => win.docId === docId) ?? windows.find((win) => win.shown === docId)
}

function bringBack(win: DocumentWindowState, mode: DocumentWindowMode): void {
  win.minimized = false
  if (mode === 'edit') win.mode = 'edit'
  win.raise += 1
}

/**
 * Opens a document's window: `view` shows the document, `edit` goes straight to its editor. A window
 * already open for the document is restored and brought to the front; `edit` turns it to the editor,
 * `view` leaves an editor that is open as it is (its unsaved text stays).
 */
export function openDocumentWindow(docId: string, mode: DocumentWindowMode = 'view'): void {
  if (!docId) return
  const open = documentWindowOf(docId)
  if (open) {
    bringBack(open, mode)
    return
  }
  const offset = (cascade++ % CASCADE_STEPS) * CASCADE_PX
  windows.push({ docId, shown: docId, mode, minimized: false, x: ORIGIN_X + offset, y: ORIGIN_Y + offset, raise: 0 })
}

/**
 * The window opened for `docId` now shows `shown` (the end of its supersede chain). When another window
 * already has that document, this one closes and the other is brought back instead (in `mode`: an edit
 * turns it to its editor, as a second open request would). Returns whether this window stays.
 */
export function followDocumentWindow(docId: string, shown: string, mode: DocumentWindowMode = 'view'): boolean {
  const win = windows.find((item) => item.docId === docId)
  if (!win) return false
  if (win.shown === shown) return true
  const other = windows.find((item) => item !== win && (item.docId === shown || item.shown === shown))
  if (!other) {
    win.shown = shown
    return true
  }
  closeDocumentWindow(docId)
  bringBack(other, mode)
  return false
}

export function setDocumentWindowMode(docId: string, mode: DocumentWindowMode): void {
  const open = windows.find((win) => win.docId === docId)
  if (open) open.mode = mode
}

export function setDocumentWindowMinimized(docId: string, minimized: boolean): void {
  const open = windows.find((win) => win.docId === docId)
  if (open) open.minimized = minimized
}

/** Closes a document's window (the window asks about unsaved text before it calls this). */
export function closeDocumentWindow(docId: string): void {
  const index = windows.findIndex((win) => win.docId === docId)
  if (index !== -1) windows.splice(index, 1)
}

/** Closes every window (sign-out, tests). */
export function closeAllDocumentWindows(): void {
  windows.splice(0, windows.length)
  cascade = 0
}
