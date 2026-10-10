// structure.0048 B15 fixture (boundary): the noopener feature turned off by a zero, next to another feature.
export function openDocument(url: string): void {
  window.open(url, '_blank', 'noreferrer,noopener=0')
}
