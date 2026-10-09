// structure.0048 B15 fixture (boundary): the second call drops 'noopener'.
export function openDocument(docId: string): void {
  window.open(`/main/?doc=${encodeURIComponent(docId)}`, '_blank', 'noopener')
}

export function openDocumentWithOpener(docId: string): void {
  window.open(`/main/?doc=${encodeURIComponent(docId)}`, '_blank')
}
