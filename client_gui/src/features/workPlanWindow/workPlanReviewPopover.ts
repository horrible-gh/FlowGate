// flowgate.v02.0073 T#2 — where the main window's review popover goes, with no screen: it is fixed on the page
// (teleported to <body>, so the window's scroll area does not clip it) and placed from its trigger's rect. Beside the
// trigger first, inside the window the trigger is in, so the rows below stay reachable (the mockup's order); else
// below it, else above it; always kept inside the viewport, on every path — also for a trigger scrolled or dragged
// (with its window) off the viewport's side.

export interface PopoverRect {
  readonly left: number
  readonly top: number
  readonly right: number
  readonly bottom: number
}

export interface PopoverSize {
  readonly width: number
  readonly height: number
}

/** Space kept to the viewport's and the window's edges. */
export const REVIEW_POPOVER_MARGIN = 8
/** Space between the trigger and the popover. */
export const REVIEW_POPOVER_GAP = 6

const clamp = (value: number, min: number, max: number) => Math.min(Math.max(value, min), Math.max(min, max))

/**
 * The popover's left / top (viewport px). `bounds` is the rect of the window the trigger is in (null: the viewport):
 * beside the trigger (right, then left) when it fits there — both its edges inside the window and the viewport — its
 * top on the trigger's; else under the trigger, its right edge on the trigger's, or over it when there is no room under.
 * Every result is clamped to the viewport's margins; a popover larger than the viewport sticks to the top-left margin.
 */
export function reviewPopoverPlace(trigger: PopoverRect, size: PopoverSize, viewport: PopoverSize, bounds: PopoverRect | null = null): { left: number; top: number } {
  const m = REVIEW_POPOVER_MARGIN
  const maxLeft = viewport.width - size.width - m
  const maxTop = viewport.height - size.height - m
  const limitLeft = Math.max(m, (bounds?.left ?? 0) + m)
  const limitRight = Math.min(viewport.width - m, (bounds?.right ?? viewport.width) - m)
  const fits = (x: number) => x >= limitLeft && x + size.width <= limitRight
  const at = (x: number, y: number) => ({ left: clamp(x, m, maxLeft), top: clamp(y, m, maxTop) })
  const right = trigger.right + REVIEW_POPOVER_GAP
  const left = trigger.left - REVIEW_POPOVER_GAP - size.width
  if (fits(right)) return at(right, trigger.top)
  if (fits(left)) return at(left, trigger.top)
  const x = trigger.right - size.width
  const below = trigger.bottom + REVIEW_POPOVER_GAP
  const above = trigger.top - REVIEW_POPOVER_GAP - size.height
  if (below + size.height <= viewport.height - m) return at(x, below)
  if (above >= m) return at(x, above)
  return at(x, below)
}
