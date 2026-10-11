// flowgate.v02.0048 T#4 — ↗ 열기 (D#1 §9.2). Every Inspector feature opens a document through this
// one function, so where a document is shown is decided here only.
// flowgate.v02.0053 T#3 — the document opens in this tab, in its own modeless document window (MD
// Viewer, ✎ editing in the same window; features/documentWindow). The v0.1 new-tab path is gone: no
// second tab, no second sign-in, no v0.1 screen. One window per document — opening it again brings
// the open window back.
// - `view` (↗): the document as it reads.
// - `edit` (✎): straight to the window's text editor.
import { openDocumentWindow, type DocumentWindowMode } from '@/features/documentWindow'

export type OpenDocumentMode = DocumentWindowMode

/** Opens the document in its document window: `view` (default) or straight to the editor (`edit`). */
export function openDocument(docId: string, mode: OpenDocumentMode = 'view'): void {
  if (docId) openDocumentWindow(docId, mode)
}
