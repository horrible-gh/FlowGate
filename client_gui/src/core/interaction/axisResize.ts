// Shared single-axis drag/keyboard resize interaction (T0010 §7/§12).
//
// SplitPane's splitter and ResizablePane's edge handle both resize one numeric value
// along one axis: mousedown starts a drag, document-level mousemove/mouseup track and
// end it, and an arrow-key pair (plus Home/End) steps the same value from the keyboard.
// That lifecycle -- listener registration, delta tracking, min/max clamping through a
// caller-supplied function, and listener cleanup -- is identical between the two
// components; only *how* a pointer coordinate maps to "this axis" and *how* a value is
// clamped differ, so those are the only two things a caller supplies (T0010 §7: extract
// only because the duplication is real, not to build a general layout manager).
export interface AxisResizeCallbacks {
  /** Current value, read fresh each time (so `onEnd` reports the true final value). */
  getValue: () => number
  /** Reads the coordinate that matters for this axis (e.g. `event.clientX`), with the
   *  sign already applied so that "drag toward larger value" always maps to "increase". */
  getPointerPos: (event: MouseEvent) => number
  clamp: (value: number) => number
  onChange: (value: number) => void
  onEnd: (value: number) => void
}

export interface AxisResizeController {
  onMouseDown: (event: MouseEvent) => void
  /** `decreaseKey`/`increaseKey` are the two arrow keys for this axis (already
   *  direction/edge-adjusted by the caller); Home/End jump to `min`/`max`. */
  onKeyDown: (event: KeyboardEvent, step: number, decreaseKey: string, increaseKey: string, min: number, max: number) => void
  /** Removes any in-flight document listeners. Safe to call unconditionally on unmount. */
  dispose: () => void
}

export function createAxisResize(callbacks: AxisResizeCallbacks): AxisResizeController {
  let dragging = false
  let dragStartPos = 0
  let dragStartValue = 0
  let previousUserSelect = ''

  function onMove(event: MouseEvent): void {
    if (!dragging) return
    const delta = callbacks.getPointerPos(event) - dragStartPos
    callbacks.onChange(callbacks.clamp(dragStartValue + delta))
  }

  function onUp(): void {
    if (!dragging) return
    dragging = false
    document.removeEventListener('mousemove', onMove)
    document.removeEventListener('mouseup', onUp)
    document.body.style.userSelect = previousUserSelect
    callbacks.onEnd(callbacks.getValue())
  }

  function onMouseDown(event: MouseEvent): void {
    // Only the primary (typically left) button starts a drag -- a right-click meant to
    // open a context menu, or a middle-click, must not also move the splitter/handle
    // just because it happened to land on it. `preventDefault` plus a temporary
    // `user-select: none` on `<body>` suppress the native text-selection/drag-start
    // that mousedown+mousemove would otherwise trigger on surrounding text, which is
    // restored the moment the drag ends (mouseup or an in-flight `dispose`).
    if (event.button !== 0) return
    event.preventDefault()
    dragging = true
    dragStartPos = callbacks.getPointerPos(event)
    dragStartValue = callbacks.getValue()
    previousUserSelect = document.body.style.userSelect
    document.body.style.userSelect = 'none'
    document.addEventListener('mousemove', onMove)
    document.addEventListener('mouseup', onUp)
  }

  function onKeyDown(
    event: KeyboardEvent,
    step: number,
    decreaseKey: string,
    increaseKey: string,
    min: number,
    max: number,
  ): void {
    let next: number | null = null
    if (event.key === decreaseKey) next = callbacks.getValue() - step
    else if (event.key === increaseKey) next = callbacks.getValue() + step
    else if (event.key === 'Home') next = min
    else if (event.key === 'End') next = max
    if (next === null) return
    event.preventDefault()
    const clamped = callbacks.clamp(next)
    callbacks.onChange(clamped)
    callbacks.onEnd(clamped)
  }

  function dispose(): void {
    document.removeEventListener('mousemove', onMove)
    document.removeEventListener('mouseup', onUp)
    if (dragging) document.body.style.userSelect = previousUserSelect
    dragging = false
  }

  return { onMouseDown, onKeyDown, dispose }
}
