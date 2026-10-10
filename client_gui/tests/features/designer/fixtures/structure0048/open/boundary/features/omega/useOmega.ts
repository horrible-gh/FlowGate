// structure.0048 B15 fixture (boundary): a feature opening a tab itself, even with 'noopener'.
export function useOmega(docId: string) {
  return { open: () => window.open(`/main/?doc=${docId}`, '_blank', 'noopener') }
}
