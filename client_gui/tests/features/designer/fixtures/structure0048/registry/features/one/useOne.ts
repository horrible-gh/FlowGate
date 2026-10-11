// structure.0048 boundary fixture (flowgate.v02.0051 B14): a composable that operates an undecided
// feature — the call is the violation, the message key 'memo' of this fixture is not (flowgate.v02.0053 T#9:
// the memo is decided; the undecided feature operated here is the AI delegation).
function delegateSave(text: string): string {
  return text
}

export function useOne(): string {
  // a delegation is saved here (a comment naming delegateSave( is not source)
  return delegateSave('x')
}
