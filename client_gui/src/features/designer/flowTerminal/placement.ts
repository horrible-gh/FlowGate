// Where and how big a terminal window first opens: 760 × 460, shrunk to the viewport minus 25 px, fully on screen.
// Later windows step right and down by `position`, like the attachment windows.
export const TERMINAL_WIDTH = 760
export const TERMINAL_HEIGHT = 460
const VIEW_MARGIN = 25

export interface TerminalRect { x: number; y: number; width: number; height: number }

export function initialTerminalRect(viewWidth: number, viewHeight: number, position = 0): TerminalRect {
  const width = Math.max(0, Math.min(TERMINAL_WIDTH, viewWidth - VIEW_MARGIN))
  const height = Math.max(0, Math.min(TERMINAL_HEIGHT, viewHeight - VIEW_MARGIN))
  const x = Math.max(220, Math.round(viewWidth * 0.24)) + position * 40
  const y = Math.max(150, Math.round(viewHeight * 0.3)) + position * 34
  return {
    x: Math.min(x, Math.max(10, viewWidth - width - 15)),
    y: Math.min(y, Math.max(8, viewHeight - height - 15)),
    width,
    height,
  }
}
