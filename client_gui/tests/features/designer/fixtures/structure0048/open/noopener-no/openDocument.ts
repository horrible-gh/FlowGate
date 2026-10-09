// structure.0048 B15 fixture (boundary): the noopener feature turned off.
export function openDocument(url: string): void {
  window.open(url, '_blank', 'noopener=no')
}
