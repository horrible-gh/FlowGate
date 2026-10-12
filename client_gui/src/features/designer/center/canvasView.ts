// flowgate.v02.0026 — per-Flow Canvas view state (0003-D §3.3.1): Viewport + single Selection.
// The zoom range lives here and only here (initial values from 0003-D §3.3.2, to be tuned
// after the browser check).
//
// flowgate.v02.0059 T#1 (0011-CH contract A / B): the Selection is a set of Nodes
// (`selectedNodeIds`); `selectedNodeId` stays as the value derived from it — the Node when exactly
// one is selected, else null — so its readers (Inspector, MiniMap, Delete) are unchanged. Both are
// only ever written together, through setViewSelection. A group of dragged Nodes is kept inside the
// Canvas origin as a whole (clampGroupToCanvas), so its relative layout survives the bound.

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
 */
export function clampGroupToCanvas<T extends { readonly x: number; readonly y: number }>(items: readonly T[]): T[] {
  if (items.length === 0) return []
  const dx = Math.max(0, -Math.min(...items.map((item) => item.x)))
  const dy = Math.max(0, -Math.min(...items.map((item) => item.y)))
  return items.map((item) => ({ ...item, x: item.x + dx, y: item.y + dy }))
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
