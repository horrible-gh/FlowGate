// structure.0048 B15 fixture (clean, flowgate.v02.0053 T#3): the document opens in the in-tab document
// window; no browser tab is opened.
import { openDocumentWindow } from '@/features/documentWindow'

export function openDocument(docId: string, mode: 'view' | 'edit' = 'view'): void {
  openDocumentWindow(docId, mode)
}
