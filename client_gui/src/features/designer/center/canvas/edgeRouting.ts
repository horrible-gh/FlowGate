import { Position } from '@vue-flow/core'
import { edgeAnchors, type EdgeAnchors, type NodeRect } from './edgeGeometry'

export interface RoutePoint { x: number; y: number }
export interface EdgeRoute { anchors: EdgeAnchors; points: RoutePoint[] }
const outward: Record<Position, RoutePoint> = {
  [Position.Left]: { x: -1, y: 0 }, [Position.Right]: { x: 1, y: 0 },
  [Position.Top]: { x: 0, y: -1 }, [Position.Bottom]: { x: 0, y: 1 },
}
/** Strict interior intersection: travelling along the clearance boundary is safe. */
export function segmentCrossesRect(a: RoutePoint, b: RoutePoint, r: NodeRect): boolean {
  if (a.x === b.x) return a.x > r.x && a.x < r.x + r.width
    && Math.max(a.y, b.y) > r.y && Math.min(a.y, b.y) < r.y + r.height
  if (a.y === b.y) return a.y > r.y && a.y < r.y + r.height
    && Math.max(a.x, b.x) > r.x && Math.min(a.x, b.x) < r.x + r.width
  return true // router only emits orthogonal segments
}

/** A direct orthogonal route. Other cards deliberately do not divert the line. */
export function routeEdge(source: NodeRect, target: NodeRect): EdgeRoute {
  return routeFromAnchors(edgeAnchors(source, target))
}

function routeFromAnchors(anchors: EdgeAnchors, offset?: number): EdgeRoute {
  const start = { x: anchors.sourceX, y: anchors.sourceY }
  const end = { x: anchors.targetX, y: anchors.targetY }
  const direction = outward[anchors.targetPosition]
  const horizontal = direction.x !== 0
  // Shared inputs bend halfway across the nearest source-to-target gap.
  // A single connection uses its own port midpoint.
  const gap = (start.x - end.x) * direction.x + (start.y - end.y) * direction.y
  const clearance = offset === undefined ? gap / 2 : gap > 0 ? Math.min(offset, gap / 2) : offset
  const trunk = horizontal ? end.x + direction.x * clearance : end.y + direction.y * clearance
  return { anchors, points: [start,
    horizontal ? { x: trunk, y: start.y } : { x: start.x, y: trunk },
    horizontal ? { x: trunk, y: end.y } : { x: end.x, y: trunk }, end] }
}

export interface RoutedEdge extends EdgeRoute { drawArrow: boolean }

/** Compute the whole canvas together. Inputs of a card share a trunk and one arrow.
 * Paths remain individually
 * selectable on their branches; sharing never changes execution or deletion permissions.
 */
export function routeEdges(
  edges: readonly { id: string; source: string; target: string }[],
  nodes: ReadonlyMap<string, NodeRect>,
): Map<string, RoutedEdge> {
  const result = new Map<string, RoutedEdge>()
  const trunks = new Map<string, number>()
  const arrows = new Set<string>()
  for (const edge of edges) {
    const source = nodes.get(edge.source), target = nodes.get(edge.target)
    if (!source || !target || edge.source === edge.target) continue
    const anchors = edgeAnchors(source, target)
    const key = `${edge.target}:${anchors.targetPosition}`
    // Use the closest source so the shared bend stays between every input and target.
    const direction = outward[anchors.targetPosition]
    const gap = (anchors.sourceX - anchors.targetX) * direction.x
      + (anchors.sourceY - anchors.targetY) * direction.y
    trunks.set(key, Math.min(trunks.get(key) ?? Infinity, gap / 2))
    result.set(edge.id, { anchors, points: [], drawArrow: !arrows.has(key) })
    arrows.add(key)
  }
  for (const edge of edges) {
    const route = result.get(edge.id)
    if (!route) continue
    const key = `${edge.target}:${route.anchors.targetPosition}`
    route.points = routeFromAnchors(route.anchors, trunks.get(key)).points
  }
  return result
}

/** The path stops at the arrow base; its label is at half the travelled length. */
export function routeDrawing(route: EdgeRoute, arrowLength = 9): { path: string; middle: RoutePoint } {
  const points = route.points.map(p => ({ ...p }))
  const end = points[points.length - 1]!
  const direction = outward[route.anchors.targetPosition]
  end.x += direction.x * arrowLength
  end.y += direction.y * arrowLength
  const lengths = points.slice(1).map((p, i) => Math.abs(p.x - points[i]!.x) + Math.abs(p.y - points[i]!.y))
  let remaining = lengths.reduce((sum, length) => sum + length, 0) / 2
  let middle = points[0]!
  for (let i = 0; i < lengths.length; i++) {
    const length = lengths[i]!
    if (remaining <= length && length > 0) {
      const a = points[i]!, b = points[i + 1]!, ratio = remaining / length
      middle = { x: a.x + (b.x - a.x) * ratio, y: a.y + (b.y - a.y) * ratio }
      break
    }
    remaining -= length
  }
  return { path: points.map((p, i) => `${i ? 'L' : 'M'}${p.x},${p.y}`).join(' '), middle }
}
