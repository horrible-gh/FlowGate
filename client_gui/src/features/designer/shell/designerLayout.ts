// flowgate.v02.0025 — Designer Shell layout state (0003-D §3.8).
//
// Screen state owned by one DesignerShell instance: which left Explorer is showing, whether
// the left Dock is collapsed, and the Dock sizes the Body / CenterColumn apply. It is not a
// Pinia store and is never persisted — leaving the Designer and coming back starts again
// from these initial values. Children receive it as props and ask the Shell to change it
// through events.
//
// The sizes are initial proposals to review on the real screen (0025-R §11), not final UX
// values. flowgate.v02.0046: the Body / CenterColumn resize handles change these same fields
// (leftWidth / rightWidth / bottomHeight) within the bounds below.
// flowgate.v02.0036: the bottom Dock grew from 200 to 300px so the Inspector (0035 v7: 72px
// header + tabs above its content) keeps a usable Content area next to the Canvas.
// flowgate.v02.0056 (0017-CH): 300 -> 320px, so the empty attachments tab (illustration, two lines and
// [+ 파일 추가], about 223px) fits the Content area (dock - 1px line - 72px header/tabs - 24px padding) unscrolled.
// flowgate.v02.0037: the right Dock became the Tool Panel; which tool it shows is layout state
// too, so the choice survives a collapse the same way the left Explorer mode does.

export type DesignerLeftMode = 'project' | 'file'

export const DESIGNER_LEFT_MODES: readonly DesignerLeftMode[] = ['project', 'file']

/** flowgate.v02.0037 — the right Tool Panel tabs, in display order (팔레트 / 대화 / Git). */
export type DesignerRightTool = 'palette' | 'conversation' | 'git'

export const DESIGNER_RIGHT_TOOLS: readonly DesignerRightTool[] = ['palette', 'conversation', 'git']

export interface DesignerLayoutState {
  activeLeftMode: DesignerLeftMode
  leftCollapsed: boolean
  /** Expanded left Dock width in px. Kept while collapsed. */
  leftWidth: number
  /** The right Tool Panel tab that is showing. */
  activeRightTool: DesignerRightTool
  /** No trigger yet (Right collapse UI is a later group); the Body already honours it. */
  rightCollapsed: boolean
  rightWidth: number
  bottomHeight: number
}

/** Width of a collapsed Dock (the rail that keeps the expand button). */
export const DESIGNER_RAIL_WIDTH = 40
/** The Center column never shrinks below this; the Body scrolls sideways instead. */
export const DESIGNER_CENTER_MIN_WIDTH = 360

/**
 * flowgate.v02.0036 — the bottom Dock splits Inspector 7 : Minimap 3 (0035 v7). The Inspector
 * track never drops below this, so at the 360px Center minimum it still holds every v7 tab
 * (see inspectorTabStripMinWidth); the Minimap placeholder gives way first.
 */
export const DESIGNER_BOTTOM_INSPECTOR_MIN_WIDTH = 264
export const DESIGNER_BOTTOM_MINIMAP_MIN_WIDTH = 96

/** grid-template-columns of the bottom Dock: Inspector | Minimap. */
export const DESIGNER_BOTTOM_DOCK_COLUMNS =
  `minmax(${DESIGNER_BOTTOM_INSPECTOR_MIN_WIDTH}px, 7fr) minmax(${DESIGNER_BOTTOM_MINIMAP_MIN_WIDTH}px, 3fr)`

export const DESIGNER_LAYOUT_DEFAULTS: Readonly<DesignerLayoutState> = Object.freeze({
  activeLeftMode: 'project',
  leftCollapsed: false,
  leftWidth: 280,
  activeRightTool: 'palette',
  rightCollapsed: false,
  rightWidth: 300,
  bottomHeight: 320,
})

export function createDesignerLayout(): DesignerLayoutState {
  return { ...DESIGNER_LAYOUT_DEFAULTS }
}

/**
 * flowgate.v02.0046 — drag resize bounds. A Dock never goes below its minimum; it only grows
 * while the Center keeps DESIGNER_CENTER_MIN_WIDTH (the Workspace DESIGNER_WORKSPACE_MIN_HEIGHT
 * above the bottom Dock). When there is no such room left (a narrow window) the Dock can still
 * shrink but not grow past its current size, and the Body scrolls sideways as before.
 */
export const DESIGNER_LEFT_MIN_WIDTH = 200
export const DESIGNER_RIGHT_MIN_WIDTH = 240
export const DESIGNER_BOTTOM_MIN_HEIGHT = 160
export const DESIGNER_WORKSPACE_MIN_HEIGHT = 160

/** `size` kept within [min, max(min, room, current)], in whole px. */
export function clampDockSize(size: number, min: number, room: number, current: number): number {
  const max = Math.max(min, room, current)
  return Math.round(Math.min(Math.max(size, min), max))
}

/** Left Dock width for a drag to `width`, in a Body `bodyWidth` px wide. */
export function clampLeftWidth(layout: DesignerLayoutState, width: number, bodyWidth: number): number {
  const room = bodyWidth - rightColumnWidth(layout) - DESIGNER_CENTER_MIN_WIDTH
  return clampDockSize(width, DESIGNER_LEFT_MIN_WIDTH, room, layout.leftWidth)
}

/** Right Dock width for a drag to `width`, in a Body `bodyWidth` px wide. */
export function clampRightWidth(layout: DesignerLayoutState, width: number, bodyWidth: number): number {
  const room = bodyWidth - leftColumnWidth(layout) - DESIGNER_CENTER_MIN_WIDTH
  return clampDockSize(width, DESIGNER_RIGHT_MIN_WIDTH, room, layout.rightWidth)
}

/** Bottom Dock height for a drag to `height`, in a Center column `columnHeight` px high. */
export function clampBottomHeight(layout: DesignerLayoutState, height: number, columnHeight: number): number {
  const room = columnHeight - DESIGNER_WORKSPACE_MIN_HEIGHT
  return clampDockSize(height, DESIGNER_BOTTOM_MIN_HEIGHT, room, layout.bottomHeight)
}

/** Width the Body gives the left column for the current state. */
export function leftColumnWidth(layout: DesignerLayoutState): number {
  return layout.leftCollapsed ? DESIGNER_RAIL_WIDTH : layout.leftWidth
}

/** Width the Body gives the right column for the current state. */
export function rightColumnWidth(layout: DesignerLayoutState): number {
  return layout.rightCollapsed ? DESIGNER_RAIL_WIDTH : layout.rightWidth
}
