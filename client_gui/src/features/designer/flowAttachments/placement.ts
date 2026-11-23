// Where and how big an attachment window first opens (0074 · item 5-2): 720 × 600, shrunk to the viewport minus
// 25 px on a small screen, and placed so that the whole window stays on screen. Later windows step right and down by
// `position`, as before. Resizing by the corner (CSS resize) and dragging by the header are unchanged; the window has
// no saved size, so every window opens at this size.
export const WINDOW_WIDTH = 720
export const WINDOW_HEIGHT = 600
/** The window never opens larger than the viewport minus this margin. */
export const VIEW_MARGIN = 25

export interface WindowRect { x: number; y: number; width: number; height: number }

export function initialRect(viewWidth: number, viewHeight: number, position = 0): WindowRect {
  const width = Math.max(0, Math.min(WINDOW_WIDTH, viewWidth - VIEW_MARGIN))
  const height = Math.max(0, Math.min(WINDOW_HEIGHT, viewHeight - VIEW_MARGIN))
  const x = Math.max(245, Math.round(viewWidth * 0.28)) + position * 185
  const y = Math.max(140, Math.round(viewHeight * 0.21)) + position * 60
  return {
    x: Math.min(x, Math.max(10, viewWidth - width - 15)),
    y: Math.min(y, Math.max(8, viewHeight - height - 15)),
    width,
    height,
  }
}
