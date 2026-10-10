// structure.0048 B15 fixture (clean): features arguments that turn noopener on, written other ways.
export function openWithNoreferrer(url: string): void {
  window.open(url, '_blank', "noreferrer,noopener")
}

export function openWithYes(url: string): void {
  window.open(url, '_blank', 'popup, noopener = yes')
}

export function openWithOne(url: string): void {
  window.open(url, '_blank', `noopener=1`)
}

export function openWithParenthesesInUrl(docId: string): void {
  window.open(`/main/?doc=${encodeURIComponent(docId)}&x=(1,2)`, '_blank', 'noopener')
}
