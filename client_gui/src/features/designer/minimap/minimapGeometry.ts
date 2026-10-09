// flowgate.v02.0049 — MiniMap geometry (pure): what the MiniMap draws and how a point on it maps
// back to the Canvas. Everything is in Canvas World coordinates (FlowGraph x / y); the Canvas
// Viewport (x, y = World translation in screen pixels, zoom) and the Canvas size come from the
// Center session — the MiniMap keeps no Graph or Viewport of its own.
import type { CanvasSize, CanvasViewport } from '../center/canvasView'
import type { FlowGraph } from '../center/graph/flowGraph'

/** Size of one Node card on the Canvas (FlowNodeCard: 176 x 92). */
export const MINIMAP_NODE_WIDTH = 176
export const MINIMAP_NODE_HEIGHT = 92
/** Room kept around the drawn content, as a share of its larger side. */
export const MINIMAP_PADDING_RATIO = 0.08

export interface WorldRect {
  readonly x: number
  readonly y: number
  readonly width: number
  readonly height: number
}

export interface MiniMapNodeShape extends WorldRect {
  readonly id: string
  readonly kind: string
  readonly docType: string | null
}

export interface MiniMapEdgeLine {
  readonly id: string
  readonly x1: number
  readonly y1: number
  readonly x2: number
  readonly y2: number
}

/** How the bounds sit in the MiniMap box (SVG preserveAspectRatio xMidYMid meet). */
export interface MiniMapFit {
  readonly scale: number
  readonly offsetX: number
  readonly offsetY: number
}

export function miniMapNodes(graph: FlowGraph): MiniMapNodeShape[] {
  return graph.nodes.map((node) => ({
    id: node.id,
    kind: node.kind,
    docType: node.docType ?? null,
    x: node.x,
    y: node.y,
    width: MINIMAP_NODE_WIDTH,
    height: MINIMAP_NODE_HEIGHT,
  }))
}

/**
 * The execution Edges the Canvas draws (the same ids its vueFlowMapping keeps), as lines between
 * the facing sides of their two Nodes. `edgeIds`: the Edge ids the Canvas draws.
 */
export function miniMapEdges(graph: FlowGraph, edgeIds: ReadonlySet<string>): MiniMapEdgeLine[] {
  const byId = new Map(graph.nodes.map((node) => [node.id, node]))
  const lines: MiniMapEdgeLine[] = []
  for (const edge of graph.edges) {
    const source = byId.get(edge.source)
    const target = byId.get(edge.target)
    if (!edgeIds.has(edge.id) || !source || !target) continue
    const forward = target.x >= source.x
    lines.push({
      id: edge.id,
      x1: source.x + (forward ? MINIMAP_NODE_WIDTH : 0),
      y1: source.y + MINIMAP_NODE_HEIGHT / 2,
      x2: target.x + (forward ? 0 : MINIMAP_NODE_WIDTH),
      y2: target.y + MINIMAP_NODE_HEIGHT / 2,
    })
  }
  return lines
}

/** The World area the Canvas shows, or null while its size is unknown. */
export function viewportRect(viewport: CanvasViewport, size: CanvasSize | null): WorldRect | null {
  if (!size || size.width <= 0 || size.height <= 0 || !(viewport.zoom > 0)) return null
  return {
    x: -viewport.x / viewport.zoom,
    y: -viewport.y / viewport.zoom,
    width: size.width / viewport.zoom,
    height: size.height / viewport.zoom,
  }
}

export function unionRect(rects: readonly (WorldRect | null)[]): WorldRect | null {
  const shown = rects.filter((rect): rect is WorldRect => rect !== null)
  if (shown.length === 0) return null
  const left = Math.min(...shown.map((r) => r.x))
  const top = Math.min(...shown.map((r) => r.y))
  const right = Math.max(...shown.map((r) => r.x + r.width))
  const bottom = Math.max(...shown.map((r) => r.y + r.height))
  return { x: left, y: top, width: right - left, height: bottom - top }
}

/** Graph Nodes and the current Canvas exploration. Padding belongs only to the Nodes. */
export function miniMapBounds(nodes: readonly WorldRect[], view: WorldRect | null): WorldRect | null {
  if (nodes.length === 0) return null
  const graph = unionRect(nodes)!
  const pad = Math.max(graph.width, graph.height) * MINIMAP_PADDING_RATIO
  const left = Math.min(0, graph.x - (graph.x < 0 ? pad : 0))
  const top = Math.min(0, graph.y - (graph.y < 0 ? pad : 0))
  const right = Math.max(graph.x + graph.width + pad, view ? view.x + view.width : 0)
  const bottom = Math.max(graph.y + graph.height + pad, view ? view.y + view.height : 0)
  return { x: left, y: top, width: right - left, height: bottom - top }
}

/** Fill the panel without stretching Nodes; spare space goes right or down. */
export function miniMapPanelBounds(bounds: WorldRect, box: CanvasSize): WorldRect {
  if (!(box.width > 0) || !(box.height > 0)) return bounds
  const ratio = box.width / box.height
  return bounds.width / bounds.height < ratio
    ? { ...bounds, width: bounds.height * ratio }
    : { ...bounds, height: bounds.width / ratio }
}

/** Keep the whole Viewport inside the finite map. */
export function clampViewToMap(x: number, y: number, view: WorldRect, bounds: WorldRect): { x: number; y: number } {
  return {
    x: Math.max(bounds.x, Math.min(x, bounds.x + Math.max(0, bounds.width - view.width))),
    y: Math.max(bounds.y, Math.min(y, bounds.y + Math.max(0, bounds.height - view.height))),
  }
}

export function miniMapFit(bounds: WorldRect, box: CanvasSize): MiniMapFit | null {
  if (box.width <= 0 || box.height <= 0 || bounds.width <= 0 || bounds.height <= 0) return null
  const scale = Math.min(box.width / bounds.width, box.height / bounds.height)
  return { scale, offsetX: (box.width - bounds.width * scale) / 2, offsetY: (box.height - bounds.height * scale) / 2 }
}

/** A point of the MiniMap box (pixels from its top-left corner) in World coordinates. */
export function boxToWorld(point: { x: number; y: number }, bounds: WorldRect, fit: MiniMapFit): { x: number; y: number } {
  return { x: bounds.x + (point.x - fit.offsetX) / fit.scale, y: bounds.y + (point.y - fit.offsetY) / fit.scale }
}

/** The Canvas Viewport whose shown area starts at World (x, y), at the same zoom. */
export function viewportAt(x: number, y: number, zoom: number): CanvasViewport {
  return { x: -x * zoom, y: -y * zoom, zoom }
}

export function containsPoint(rect: WorldRect, point: { x: number; y: number }): boolean {
  return point.x >= rect.x && point.x <= rect.x + rect.width && point.y >= rect.y && point.y <= rect.y + rect.height
}
