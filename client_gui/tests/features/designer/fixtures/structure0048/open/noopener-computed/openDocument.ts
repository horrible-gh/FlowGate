// structure.0048 B15 fixture (boundary): features held in a variable cannot be read from the source.
const features = 'noopener'

export function openDocument(url: string): void {
  window.open(url, '_blank', features)
}
