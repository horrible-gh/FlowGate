// flowgate.v02.0025 — Designer Shell layout state (0003-D §3.8).
//
// Screen state owned by one DesignerShell instance: which left Explorer is showing, whether
// the left Dock is collapsed, and the Dock sizes the Body / CenterColumn apply. It is not a
// Pinia store. Children receive it as props and ask the Shell to change it through events.
//
// flowgate.v02test.0056 (R 0056.0001 §2): the three Dock sizes (leftWidth / rightWidth /
// bottomHeight) are saved per person through the project store (/me/ui-settings) — when a
// resize handle is let go and on 창 › 창 배치 초기화 — and read back when the Designer opens.
// The tabs and the collapsed flags are not saved: they start again from the initial values.
// These fields always hold the size the person chose. When the window has no room for it the
// Body / CenterColumn draw it smaller (fitDockWidths / fitBottomHeight) and leave these fields
// alone, so a larger window shows the chosen size again.
//
// The sizes are initial proposals to review on the real screen (0025-R §11), not final UX
// values. flowgate.v02.0046: the Body / CenterColumn resize handles change these same fields
// (leftWidth / rightWidth / bottomHeight) within the bounds below.
// flowgate.v02.0036: the bottom Dock grew from 200 to 300px so the Inspector (0035 v7: 72px
// header + tabs above its content) keeps a usable Content area next to the Canvas.
// The bottom Dock keeps its established 320px initial height for Inspector content.
// flowgate.v02.0037: the right Dock became the Tool Panel; which tool it shows is layout state
// too, so the choice survives a collapse the same way the left Explorer mode does.

export type DesignerLeftMode = 'project' | 'file'

export const DESIGNER_LEFT_MODES: readonly DesignerLeftMode[] = ['project', 'file']

/** flowgate.v02.0037 — the right Tool Panel tabs, in display order (팔레트 / 대화 / Git). */
export type DesignerRightTool = 'palette' | 'conversation' | 'git'

export const DESIGNER_RIGHT_TOOLS: readonly DesignerRightTool[] = ['conversation', 'palette', 'git']

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
/** The Center column never shrinks below this; the Docks are drawn smaller first (fitDockWidths),
 *  and below both Dock minimums the Body scrolls sideways. */
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

/** flowgate.v02test.0056 — the three saved Dock sizes, in px. */
export interface DesignerDockSizes {
  leftWidth: number
  rightWidth: number
  bottomHeight: number
}

/** The largest size the server keeps (ui_settings_service.DESIGNER_SIZE_MAX). */
export const DESIGNER_DOCK_SIZE_MAX = 4000

export const DESIGNER_DOCK_SIZE_DEFAULTS: Readonly<DesignerDockSizes> = Object.freeze({
  leftWidth: DESIGNER_LAYOUT_DEFAULTS.leftWidth,
  rightWidth: DESIGNER_LAYOUT_DEFAULTS.rightWidth,
  bottomHeight: DESIGNER_LAYOUT_DEFAULTS.bottomHeight,
})

const DOCK_SIZE_MINIMUMS: Readonly<DesignerDockSizes> = Object.freeze({
  leftWidth: DESIGNER_LEFT_MIN_WIDTH,
  rightWidth: DESIGNER_RIGHT_MIN_WIDTH,
  bottomHeight: DESIGNER_BOTTOM_MIN_HEIGHT,
})

export function dockSizesOf(layout: DesignerDockSizes): DesignerDockSizes {
  return { leftWidth: layout.leftWidth, rightWidth: layout.rightWidth, bottomHeight: layout.bottomHeight }
}

export function sameDockSizes(a: DesignerDockSizes, b: DesignerDockSizes): boolean {
  return a.leftWidth === b.leftWidth && a.rightWidth === b.rightWidth && a.bottomHeight === b.bottomHeight
}

/** A size that came from outside (the server): a whole number within its range, else the default. */
export function normalizeDockSize(key: keyof DesignerDockSizes, value: unknown): number {
  if (typeof value !== 'number' || !Number.isInteger(value)) return DESIGNER_DOCK_SIZE_DEFAULTS[key]
  if (value < DOCK_SIZE_MINIMUMS[key] || value > DESIGNER_DOCK_SIZE_MAX) return DESIGNER_DOCK_SIZE_DEFAULTS[key]
  return value
}

/** The sizes the store read from the server; a missing or strange value is its default (R §2.3). */
export function dockSizesFromStored(stored: { leftWidth: unknown; rightWidth: unknown; bottomHeight: unknown }): DesignerDockSizes {
  return {
    leftWidth: normalizeDockSize('leftWidth', stored.leftWidth),
    rightWidth: normalizeDockSize('rightWidth', stored.rightWidth),
    bottomHeight: normalizeDockSize('bottomHeight', stored.bottomHeight),
  }
}

/** Which Dock keeps its width while the other gives way first; null = both give way in proportion. */
export type DesignerFitKeep = 'left' | 'right' | null

/**
 * flowgate.v02test.0056 (R 0056.0001 §2.3) — the left / right column widths to draw in a Body
 * `bodyWidth` px wide. When the chosen widths and the Center minimum do not fit, the expanded
 * Docks are drawn smaller, never below their minimums; a collapsed Dock (its rail) is not
 * changed. Narrower than both minimums the Body scrolls sideways as before. `bodyWidth` 0 means
 * "not measured yet" and changes nothing. Only what is drawn changes — never the chosen widths.
 */
export function fitDockWidths(
  left: number,
  right: number,
  leftExpanded: boolean,
  rightExpanded: boolean,
  bodyWidth: number,
  keep: DesignerFitKeep = null,
): { left: number; right: number } {
  const excess = left + right + DESIGNER_CENTER_MIN_WIDTH - bodyWidth
  if (!(bodyWidth > 0) || excess <= 0) return { left, right }
  const leftSlack = leftExpanded ? Math.max(0, left - DESIGNER_LEFT_MIN_WIDTH) : 0
  const rightSlack = rightExpanded ? Math.max(0, right - DESIGNER_RIGHT_MIN_WIDTH) : 0
  let leftTake: number
  let rightTake: number
  if (keep === 'left') {
    rightTake = Math.min(excess, rightSlack)
    leftTake = Math.min(excess - rightTake, leftSlack)
  } else if (keep === 'right') {
    leftTake = Math.min(excess, leftSlack)
    rightTake = Math.min(excess - leftTake, rightSlack)
  } else if (leftSlack + rightSlack <= excess) {
    leftTake = leftSlack
    rightTake = rightSlack
  } else {
    leftTake = Math.min(leftSlack, Math.round((excess * leftSlack) / (leftSlack + rightSlack)))
    rightTake = Math.min(rightSlack, excess - leftTake)
  }
  return { left: Math.round(left - leftTake), right: Math.round(right - rightTake) }
}

/**
 * flowgate.v02test.0056 — the Bottom Dock height to draw in a Center column `columnHeight` px
 * high: the chosen height, or what the Workspace minimum leaves (never below the Dock minimum).
 * `columnHeight` 0 means "not measured yet" and changes nothing.
 */
export function fitBottomHeight(height: number, columnHeight: number): number {
  if (!(columnHeight > 0)) return height
  return Math.min(height, Math.max(DESIGNER_BOTTOM_MIN_HEIGHT, Math.round(columnHeight - DESIGNER_WORKSPACE_MIN_HEIGHT)))
}

/** Width the Body gives the left column for the current state. */
export function leftColumnWidth(layout: DesignerLayoutState): number {
  return layout.leftCollapsed ? DESIGNER_RAIL_WIDTH : layout.leftWidth
}

/** Width the Body gives the right column for the current state. */
export function rightColumnWidth(layout: DesignerLayoutState): number {
  return layout.rightCollapsed ? DESIGNER_RAIL_WIDTH : layout.rightWidth
}
