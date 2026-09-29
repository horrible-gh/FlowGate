// T0009 §3/§4: v0.1 (/main) and v0.2 (/app) are independent SPAs, so switching between
// them is a full-page navigation, not a client-side router push. The last choice is
// remembered under a single localStorage key so a plain /login visit (no explicit
// `next`) can return the user to whichever generation they were last on (§5).

export type GuiGeneration = 'legacy' | 'app'

const FG_UI_KEY = 'fg_ui'

const GUI_DESTINATIONS: Record<GuiGeneration, string> = {
  legacy: '/main',
  app: '/app',
}

/** Returns the stored GUI-generation choice, or null if unset/invalid (T0009 §4: "다른 임의 값은 저장하지 않는다"). */
export function getStoredGuiGeneration(): GuiGeneration | null {
  if (typeof window === 'undefined') return null
  const value = window.localStorage.getItem(FG_UI_KEY)
  return value === 'legacy' || value === 'app' ? value : null
}

export function setStoredGuiGeneration(value: GuiGeneration): void {
  if (typeof window === 'undefined') return
  window.localStorage.setItem(FG_UI_KEY, value)
}

/** Records the choice, then performs the full-page navigation to that generation's entry point. */
export function switchGuiGeneration(target: GuiGeneration): void {
  setStoredGuiGeneration(target)
  window.location.assign(GUI_DESTINATIONS[target])
}
