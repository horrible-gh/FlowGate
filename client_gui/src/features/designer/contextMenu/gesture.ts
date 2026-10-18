// flowgate.v02.0060 — right-button gesture guard for the Canvas root (0006-D rev2 §2.1, §3.1 (가)):
// the mouse right-button press and its held move / release never reach Vue Flow (no pan, card or
// group move, area / click selection, connect or placement). Left / middle buttons, wheel, touch,
// pen and keyboard pass untouched. The guarded press is relayed once to document-level listeners
// so the existing outside-press closers (menu bar, toolbar dropdown, search) still close. The
// native contextmenu is not prevented, and the guard opens no menu by itself. Attached in 2-A.

import { isEditableTarget } from './entry'

const relayedPresses = new WeakSet<Event>()
export const isRelayedPress = (event: Event): boolean => relayedPresses.has(event)

/** Document capture observers already see the original; document bubble popup listeners
 * need a relay because the canvas capture guard stops the original before its target.
 * Our menu host ignores this marked relay. No listeners or existing handlers are patched.
 */
function relayPress(event: MouseEvent, doc: Document): void {
  const init = { button: event.button, buttons: event.buttons, clientX: event.clientX, clientY: event.clientY,
    ctrlKey: event.ctrlKey, shiftKey: event.shiftKey, altKey: event.altKey, metaKey: event.metaKey }
  const copy = event.type === 'pointerdown' && typeof PointerEvent !== 'undefined'
    ? new PointerEvent(event.type, { ...init, pointerType: 'mouse', pointerId: (event as PointerEvent).pointerId })
    : new MouseEvent(event.type, init)
  relayedPresses.add(copy)
  doc.dispatchEvent(copy)
}
export function attachRightButtonGuard(element: HTMLElement): () => void {
  const doc = element.ownerDocument
  const win = doc.defaultView
  let pointerHeld = false
  let mouseHeld = false
  let nonMouse = false
  let disposed = false
  const reset = () => { pointerHeld = mouseHeld = false }
  const excluded = (event: Event) => isEditableTarget(event.target)
    || (event.target instanceof Element && !!event.target.closest('[data-designer-context-menu], [data-context-menu-exclude]'))
  const pointerMouse = (event: PointerEvent) => !event.pointerType || event.pointerType === 'mouse'
  function down(event: Event) {
    const mouse = event as MouseEvent
    if (event.type === 'pointerdown') nonMouse = !pointerMouse(event as PointerEvent)
    if (nonMouse || mouse.button !== 2 || excluded(event)) return
    if (event.type === 'pointerdown') pointerHeld = true
    else mouseHeld = true
    event.stopImmediatePropagation()
    relayPress(mouse, doc)
  }
  function moveOrUp(event: Event) {
    const pointer = event.type.startsWith('pointer')
    if (pointer ? !pointerHeld || !pointerMouse(event as PointerEvent) : !mouseHeld || nonMouse) return
    const mouse = event as MouseEvent
    // Preserve unrelated button releases during a chord.
    if (event.type.endsWith('up') && mouse.button !== 2) return
    if (event.type.endsWith('move') && !(mouse.buttons & 2)) {
      if (pointer) pointerHeld = false
      else mouseHeld = false
      return
    }
    event.stopImmediatePropagation()
    if (event.type.endsWith('up') || event.type === 'pointercancel') {
      if (pointer) pointerHeld = false
      else mouseHeld = false
    }
  }
  element.addEventListener('pointerdown', down, true)
  element.addEventListener('mousedown', down, true)
  const motions = ['pointermove', 'pointerup', 'pointercancel', 'mousemove', 'mouseup']
  motions.forEach((type) => doc.addEventListener(type, moveOrUp, true))
  doc.addEventListener('contextmenu', reset, true)
  win?.addEventListener('blur', reset)
  return () => {
    if (disposed) return
    disposed = true
    reset()
    element.removeEventListener('pointerdown', down, true)
    element.removeEventListener('mousedown', down, true)
    motions.forEach((type) => doc.removeEventListener(type, moveOrUp, true))
    doc.removeEventListener('contextmenu', reset, true)
    win?.removeEventListener('blur', reset)
  }
}

