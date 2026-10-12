// structure.0048 B15 fixture (boundary, flowgate.v02.0053 T#3): openDocument.ts itself may not open a
// browser tab either — with 'noopener' or without it.
export function openDocument(docId: string): void {
  window.open(`/main/?doc=${encodeURIComponent(docId)}`, '_blank', 'noopener')
}

export function openDocumentWithOpener(docId: string): void {
  window.open(`/main/?doc=${encodeURIComponent(docId)}`, '_blank')
}
