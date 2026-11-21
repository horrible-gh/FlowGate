import { Position } from '@vue-flow/core'
import { edgeAnchors, sideMiddle, type EdgeAnchors, type NodeRect } from './edgeGeometry'

export interface RoutePoint { x: number; y: number }
export interface EdgeRoute { anchors: EdgeAnchors; points: RoutePoint[] }
const outward: Record<Position, RoutePoint> = {
  [Position.Left]: { x: -1, y: 0 }, [Position.Right]: { x: 1, y: 0 },
  [Position.Top]: { x: 0, y: -1 }, [Position.Bottom]: { x: 0, y: 1 },
}
const inflate = (r: NodeRect, gap: number): NodeRect =>
  ({ x: r.x - gap, y: r.y - gap, width: r.width + 2 * gap, height: r.height + 2 * gap })
const inside = (p: RoutePoint, r: NodeRect) =>
  p.x > r.x && p.x < r.x + r.width && p.y > r.y && p.y < r.y + r.height

/** Strict interior intersection: travelling along the clearance boundary is safe. */
export function segmentCrossesRect(a: RoutePoint, b: RoutePoint, r: NodeRect): boolean {
  if (a.x === b.x) return a.x > r.x && a.x < r.x + r.width
    && Math.max(a.y, b.y) > r.y && Math.min(a.y, b.y) < r.y + r.height
  if (a.y === b.y) return a.y > r.y && a.y < r.y + r.height
    && Math.max(a.x, b.x) > r.x && Math.min(a.x, b.x) < r.x + r.width
  return true // router only emits orthogonal segments
}

/** A rectilinear visibility grid, including endpoint cards as obstacles.
 * BFS visits each grid point once. Coordinates come from clearance boundaries, so routes
 * cannot cut a corner. Null means overlapping cards have made the endpoint inaccessible;
 * callers must not claim that a collision-free route exists in that case.
 */
export function routeEdge(source: NodeRect, target: NodeRect, others: readonly NodeRect[], gap = 16): EdgeRoute | null {
  gap = Math.max(10, gap)
  const preferred = edgeAnchors(source, target)
  const candidates = [preferred]
  // A nearby obstacle may block a facing port while another side remains accessible.
  for (const from of Object.values(Position)) for (const to of Object.values(Position)) {
    if (from === preferred.sourcePosition && to === preferred.targetPosition) continue
    const a = sideMiddle(source, from), b = sideMiddle(target, to)
    candidates.push({ sourceX: a.x, sourceY: a.y, sourcePosition: from,
      targetX: b.x, targetY: b.y, targetPosition: to })
  }
  for (const anchors of candidates) {
    const route = routeFromAnchors(source, target, others, gap, anchors)
    if (route) return route
  }
  return null
}

function routeFromAnchors(source: NodeRect, target: NodeRect, others: readonly NodeRect[], gap: number, anchors: EdgeAnchors): EdgeRoute | null {
  const start = { x: anchors.sourceX, y: anchors.sourceY }
  const end = { x: anchors.targetX, y: anchors.targetY }
  const from = outward[anchors.sourcePosition], to = outward[anchors.targetPosition]
  const a = { x: start.x + from.x * gap, y: start.y + from.y * gap }
  const b = { x: end.x + to.x * gap, y: end.y + to.y * gap }
  const obstacles = [source, target, ...others].map(r => inflate(r, gap))
  // Only the owning card is exempt from its short anchor connector.
  if (obstacles.slice(1).some(r => segmentCrossesRect(start, a, r))
    || obstacles.filter((_, i) => i !== 1).some(r => segmentCrossesRect(b, end, r))) return null
  const xs = [...new Set([a.x, b.x, ...obstacles.flatMap(r => [r.x, r.x + r.width])])].sort((x, y) => x - y)
  const ys = [...new Set([a.y, b.y, ...obstacles.flatMap(r => [r.y, r.y + r.height])])].sort((x, y) => x - y)
  const width = xs.length
  const point = (id: number): RoutePoint => ({ x: xs[id % width]!, y: ys[Math.floor(id / width)]! })
  const first = ys.indexOf(a.y) * width + xs.indexOf(a.x)
  const last = ys.indexOf(b.y) * width + xs.indexOf(b.x)
  const parents = new Map<number, number>([[first, first]])
  const queue = [first]
  for (let head = 0; head < queue.length && !parents.has(last); head++) {
    const id = queue[head]!, x = id % width, y = Math.floor(id / width)
    const candidates = [x > 0 ? id - 1 : -1, x + 1 < width ? id + 1 : -1,
      y > 0 ? id - width : -1, y + 1 < ys.length ? id + width : -1]
    for (const next of candidates) {
      if (next < 0 || parents.has(next)) continue
      const p = point(next)
      if (obstacles.some(r => inside(p, r) || segmentCrossesRect(point(id), p, r))) continue
      parents.set(next, id)
      queue.push(next)
    }
  }
  if (!parents.has(last)) return null
  const middle: RoutePoint[] = []
  for (let id = last; ; id = parents.get(id)!) {
    middle.push(point(id))
    if (id === first) break
  }
  // Preserve the terminal stubs, including the arrow's final approach direction.
  return { anchors, points: [start, ...middle.reverse(), end] }
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
