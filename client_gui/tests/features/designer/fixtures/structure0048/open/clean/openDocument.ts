// structure.0048 B15 fixture (clean): the one place that opens a tab, with 'noopener'.
export function openDocumentUrl(docId: string): string {
  return `/main/?doc=${encodeURIComponent(docId)}`
}

export function openDocument(docId: string): void {
  window.open(openDocumentUrl(docId), '_blank', 'noopener')
}
