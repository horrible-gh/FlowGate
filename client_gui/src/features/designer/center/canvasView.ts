// flowgate.v02.0026 — per-Flow Canvas view state (0003-D §3.3.1): Viewport + single Selection.
// The zoom range lives here and only here (initial values from 0003-D §3.3.2, to be tuned
// after the browser check).

/** x, y = World translation in screen pixels, zoom = scale factor. */
export interface CanvasViewport {
  readonly x: number
  readonly y: number
  readonly zoom: number
}

export interface CanvasViewState {
  /** null = not shown yet: the Canvas fits all Nodes on first display. */
  viewport: CanvasViewport | null
  /** Single selection only. */
  selectedNodeId: string | null
}

export const CANVAS_MIN_ZOOM = 0.25
export const CANVAS_MAX_ZOOM = 2
/** The initial fit never enlarges a small Graph beyond 1x. */
export const CANVAS_FIT_MAX_ZOOM = 1

/** Viewport of a Graph with no Nodes: no translation, 1x. */
export const DEFAULT_CANVAS_VIEWPORT: CanvasViewport = Object.freeze({ x: 0, y: 0, zoom: 1 })

export function createCanvasViewState(): CanvasViewState {
  return { viewport: null, selectedNodeId: null }
}

export function clampCanvasZoom(zoom: number): number {
  return Math.min(CANVAS_MAX_ZOOM, Math.max(CANVAS_MIN_ZOOM, zoom))
}

export function sameViewport(a: CanvasViewport | null, b: CanvasViewport | null): boolean {
  if (!a || !b) return a === b
  return a.x === b.x && a.y === b.y && a.zoom === b.zoom
}
