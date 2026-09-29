// Shared trigger-focus capture/restore (T0006 §9/§11).
//
// Both ModalShell (mandatory) and ModelessShell (courtesy, only when focus was still
// inside the panel at close time) need to remember what had focus before they opened
// and try to put focus back there when they close. Extracted because both shells
// duplicated the exact same three-step logic otherwise (T0006 §9 rule: 2+ real
// duplicates -> shared module).
export function captureFocusedElement(): HTMLElement | null {
  const active = document.activeElement
  return active instanceof HTMLElement ? active : null
}

export function returnFocusTo(el: HTMLElement | null): void {
  if (el == null) return
  if (!document.contains(el)) return
  try {
    el.focus()
  } catch {
    // Best effort; a focus failure must never break closing an overlay.
  }
}
