// flowgate.v02.0026 — per-Flow Canvas view state (0003-D §3.3.1): Viewport + single Selection.
// The zoom range lives here and only here (initial values from 0003-D §3.3.2, to be tuned
// after the browser check).
//
// flowgate.v02.0059 T#1 (0011-CH contract A / B): the Selection is a set of Nodes
// (`selectedNodeIds`); `selectedNodeId` stays as the value derived from it — the Node when exactly
// one is selected, else null — so its readers (Inspector, MiniMap, Delete) are unchanged. Both are
// only ever written together, through setViewSelection. A group of dragged Nodes is kept inside the
// Canvas origin as a whole (clampGroupToCanvas), so its relative layout survives the bound.
//
// flowgate.v02.0059 0017-T: a drag moves in whole steps of the density's move step (stepGroupMove) — one
// offset for every dragged Node, so a dragged selection keeps its relative layout as well.
// 0018-TR rework: the step is a lattice of Graph points — the dragged card lands on one of them, and the
// Canvas dot grid (canvasDotGrid) draws exactly those points, so a dropped card sits on a dot.

/** x, y = World translation in screen pixels, zoom = scale factor. */
export interface CanvasViewport {
  readonly x: number
  readonly y: number
  readonly zoom: number
}

/** flowgate.v02.0049: the Canvas area in screen pixels (what the MiniMap shows as the visible area). */
export interface CanvasSize {
  readonly width: number
  readonly height: number
}

export interface CanvasViewState {
  /** null = not shown yet: the Canvas fits all Nodes on first display. */
  viewport: CanvasViewport | null
  /** flowgate.v02.0059 T#1: every selected Node, without repeats (several only in 선택 mode). */
  selectedNodeIds: string[]
  /** The selected Node when exactly one is selected, else null (derived from selectedNodeIds). */
  selectedNodeId: string | null
}

export const CANVAS_MIN_ZOOM = 0.25
export const CANVAS_MAX_ZOOM = 2
/** The initial fit never enlarges a small Graph beyond 1x. */
export const CANVAS_FIT_MAX_ZOOM = 1

/** Viewport of a Graph with no Nodes: no translation, 1x. */
export const DEFAULT_CANVAS_VIEWPORT: CanvasViewport = Object.freeze({ x: 0, y: 0, zoom: 1 })

export function createCanvasViewState(): CanvasViewState {
  return { viewport: null, selectedNodeIds: [], selectedNodeId: null }
}

/**
 * flowgate.v02.0059 T#1: sets a view's Selection (repeats dropped, order kept) and its derived
 * single Node. Whether anything changed.
 */
export function setViewSelection(view: CanvasViewState, nodeIds: readonly string[]): boolean {
  const next = [...new Set(nodeIds)]
  const same = next.length === view.selectedNodeIds.length && next.every((id, index) => view.selectedNodeIds[index] === id)
  const single = next.length === 1 ? next[0] : null
  if (same && view.selectedNodeId === single) return false
  view.selectedNodeIds = next
  view.selectedNodeId = single
  return true
}

/**
 * flowgate.v02.0059 T#1 (contract B): dropped positions of a dragged group, moved together just
 * enough that none is left of / above the Canvas origin — one offset for the whole group, so the
 * Nodes keep their relative layout. A single Node is clamped as before (each axis to 0 or more).
 * 0018-TR rework: with a move `step` the group is pushed in whole steps, so a card on the step lattice stays on it.
 */
export function clampGroupToCanvas<T extends { readonly x: number; readonly y: number }>(
  items: readonly T[],
  step?: number | null,
): T[] {
  if (items.length === 0) return []
  const push = (overflow: number) => (step && step > 0 ? Math.ceil(overflow / step) * step : overflow)
  const dx = push(Math.max(0, -Math.min(...items.map((item) => item.x))))
  const dy = push(Math.max(0, -Math.min(...items.map((item) => item.y))))
  return items.map((item) => ({ ...item, x: item.x + dx, y: item.y + dy }))
}

/**
 * flowgate.v02.0059 0017-T: dragged positions moved in whole steps of `step` (the density's move step,
 * top/designerTop.ts DENSITY_MOVE_STEP). 0018-TR rework: the anchor Node (the one under the pointer, else the
 * first) goes to the step lattice point nearest to where it was dragged — the Graph points whose x and y are
 * whole steps, which the Canvas draws as its dots (canvasDotGrid) — rounded the same in both directions. Every
 * other dragged Node gets the anchor's offset from its own start, so the Nodes keep their relative layout.
 * No step (자율형) leaves every position as it was dragged, and so does a start that is not known.
 */
export function stepGroupMove<T extends { readonly nodeId: string; readonly x: number; readonly y: number }>(
  moved: readonly T[],
  startOf: (nodeId: string) => { readonly x: number; readonly y: number } | null | undefined,
  step: number | null | undefined,
  anchorId?: string | null,
): T[] {
  const anchor = moved.find((item) => item.nodeId === anchorId) ?? moved[0]
  const from = anchor ? startOf(anchor.nodeId) : null
  const starts = moved.map((item) => startOf(item.nodeId))
  if (!step || !(step > 0) || !anchor || !from || starts.some((start) => !start)) return moved.map((item) => ({ ...item }))
  const dx = snapToStep(anchor.x, step) - from.x
  const dy = snapToStep(anchor.y, step) - from.y
  return moved.map((item, index) => ({ ...item, x: starts[index]!.x + dx, y: starts[index]!.y + dy }))
}

/** A coordinate on the nearest whole step, half a step away from zero (left / up as right / down). */
function snapToStep(value: number, step: number): number {
  const steps = Math.round(Math.abs(value) / step)
  return steps === 0 ? 0 : Math.sign(value) * steps * step
}

/** 0018-TR rework: dots closer than this on screen (px) are drawn every second (fourth, ...) step instead. */
export const CANVAS_GRID_MIN_SCREEN_GAP = 4

/** 0018-TR rework: the Canvas dot grid as drawn — `gap` in Graph units, `size` / `x` / `y` in screen pixels. */
export interface CanvasDotGrid {
  /** Graph distance between two dots: the move step (doubled while that is closer than the screen minimum). */
  readonly gap: number
  /** Screen distance between two dots (gap × zoom). */
  readonly size: number
  /** Screen position of the Graph origin, which is a dot (the Viewport translation). */
  readonly x: number
  readonly y: number
}

/**
 * 0018-TR rework: the dot grid of a Viewport and a move step — one dot on every Graph point whose x and y are
 * whole `gap`s, so it moves and scales with the Canvas (pan / zoom) and a card dropped on the step lattice sits
 * on a dot. `gap` is the step itself, or its double (quadruple, ...) only while the step would be drawn closer
 * than CANVAS_GRID_MIN_SCREEN_GAP pixels (far zoomed out), so every dot is still a lattice point. No step
 * (자율형) = no lattice = no grid (null).
 */
export function canvasDotGrid(viewport: CanvasViewport, step: number | null | undefined): CanvasDotGrid | null {
  if (!step || !(step > 0) || !(viewport.zoom > 0)) return null
  let gap = step
  while (gap * viewport.zoom < CANVAS_GRID_MIN_SCREEN_GAP) gap *= 2
  return { gap, size: gap * viewport.zoom, x: viewport.x, y: viewport.y }
}

export function clampCanvasZoom(zoom: number): number {
  return Math.min(CANVAS_MAX_ZOOM, Math.max(CANVAS_MIN_ZOOM, zoom))
}

export function sameCanvasSize(a: CanvasSize | null, b: CanvasSize | null): boolean {
  if (!a || !b) return a === b
  return a.width === b.width && a.height === b.height
}

export function sameViewport(a: CanvasViewport | null, b: CanvasViewport | null): boolean {
  if (!a || !b) return a === b
  return a.x === b.x && a.y === b.y && a.zoom === b.zoom
}
