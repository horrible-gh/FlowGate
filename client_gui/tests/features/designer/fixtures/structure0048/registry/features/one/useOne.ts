// structure.0048 boundary fixture (flowgate.v02.0051 B14): a composable that operates an undecided
// feature — the call is the violation, the message key 'memo' of this fixture is not.
function memoSave(text: string): string {
  return text
}

export function useOne(): string {
  // a memo is saved here (a comment naming memoSave( is not source)
  return memoSave('x')
}
