import { Position } from '@vue-flow/core'
import { edgeAnchors, facingSide, sideMiddle, type EdgeAnchors, type NodeRect } from './edgeGeometry'

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

const CLEARANCE = 12
const sides = [Position.Right, Position.Bottom, Position.Left, Position.Top]
const opposite = (side: Position): Position => sides[(sides.indexOf(side) + 2) % 4]!
const horizontal = (side: Position): boolean => outward[side].x !== 0
const distance = (a: RoutePoint, b: RoutePoint): number => Math.abs(a.x - b.x) + Math.abs(a.y - b.y)
const inflate = (r: NodeRect): NodeRect => ({ x: r.x - CLEARANCE, y: r.y - CLEARANCE,
  width: r.width + 2 * CLEARANCE, height: r.height + 2 * CLEARANCE })

function simplify(points: RoutePoint[]): RoutePoint[] {
  const out: RoutePoint[] = []
  for (const p of points) {
    const a = out.at(-1), b = out.at(-2)
    if (a && distance(a, p) === 0) continue
    if (a && b && ((b.x === a.x && a.x === p.x) || (b.y === a.y && a.y === p.y))) out.pop()
    out.push(p)
  }
  return out
}
type Ports = ReadonlyMap<NodeRect, ReadonlyMap<Position, RoutePoint>>
function targetPorts(target: NodeRect, sources: readonly NodeRect[]): Map<Position, RoutePoint> {
  const ports = new Map<Position, RoutePoint>()
  for (const side of sides) {
    const p = sideMiddle(target, side), hor = horizontal(side)
    const low = Math.max(hor ? target.y : target.x, ...sources.map(r => hor ? r.y : r.x))
    const high = Math.min(hor ? target.y + target.height : target.x + target.width,
      ...sources.map(r => hor ? r.y + r.height : r.x + r.width))
    if (low < high) {
      const value = Math.max(low, Math.min(high, hor ? p.y : p.x))
      if (hor) p.y = value
      else p.x = value
    }
    ports.set(side, p)
  }
  return ports
}
function makeRoute(anchors: EdgeAnchors, points: RoutePoint[]): EdgeRoute {
  return { anchors, points: simplify(points) }
}
function clear(points: RoutePoint[], rects: readonly NodeRect[]): boolean {
  return points.every((p, i) => !i || !rects.some(r => segmentCrossesRect(points[i - 1]!, p, r)))
}

/** A small binary heap for the rare corridor search; ordinary edges never build a grid. */
class Queue {
  private items: { id: number; cost: number }[] = []
  push(item: { id: number; cost: number }): void {
    const a = this.items
    a.push(item)
    let i = a.length - 1
    while (i > 0) {
      const p = (i - 1) >> 1
      if (a[p]!.cost <= item.cost) break
      a[i] = a[p]!
      i = p
    }
    a[i] = item
  }
  pop(): { id: number; cost: number } | undefined {
    const a = this.items, first = a[0], last = a.pop()
    if (a.length && last) {
      let i = 0
      while (2 * i + 1 < a.length) {
        let c = 2 * i + 1
        if (c + 1 < a.length && a[c + 1]!.cost < a[c]!.cost) c++
        if (a[c]!.cost >= last.cost) break
        a[i] = a[c]!
        i = c
      }
      a[i] = last
    }
    return first
  }
}

/** One lazily constructed visibility grid per canvas update, shared by all detours. */
class Corridors {
  private xs: number[]
  private ys: number[]
  private links = new Map<number, number[]>()
  private bounds: NodeRect[]
  constructor(rects: readonly NodeRect[], private ports: Ports) {
    this.bounds = rects.map(inflate)
    const extra = [...ports.values()].flatMap(p => [...p.values()])
    this.xs = [...new Set([...rects.flatMap(r => [r.x - CLEARANCE, r.x + r.width / 2, r.x + r.width + CLEARANCE]), ...extra.map(p => p.x)])].sort((a, b) => a - b)
    this.ys = [...new Set([...rects.flatMap(r => [r.y - CLEARANCE, r.y + r.height / 2, r.y + r.height + CLEARANCE]), ...extra.map(p => p.y)])].sort((a, b) => a - b)
  }
  private point(id: number): RoutePoint {
    return { x: this.xs[id % this.xs.length]!, y: this.ys[Math.floor(id / this.xs.length)]! }
  }
  private port(r: NodeRect, side: Position, target = false): number {
    const p = target ? this.ports.get(r)?.get(side) ?? sideMiddle(r, side) : sideMiddle(r, side), d = outward[side]
    return this.ys.indexOf(p.y + d.y * CLEARANCE) * this.xs.length + this.xs.indexOf(p.x + d.x * CLEARANCE)
  }
  private neighbors(id: number): number[] {
    const cached = this.links.get(id)
    if (cached) return cached
    const width = this.xs.length, x = id % width, y = Math.floor(id / width)
    const candidates = [x > 0 ? id - 1 : -1, x + 1 < width ? id + 1 : -1,
      y > 0 ? id - width : -1, y + 1 < this.ys.length ? id + width : -1]
    const result = candidates.filter(n => n >= 0 && clear([this.point(id), this.point(n)], this.bounds))
    this.links.set(id, result)
    return result
  }
  route(source: NodeRect, target: NodeRect, obstacles: readonly NodeRect[]): EdgeRoute | undefined {
    const queue = new Queue(), costs = new Map<number, number>(), prev = new Map<number, number>()
    const origins = new Map<number, Position>(), goals = new Map<number, Position>()
    const preferred = facingSide(source, target)
    for (const side of [preferred, ...sides.filter(s => s !== preferred)]) {
      const a = this.port(source, side), b = this.port(target, side, true)
      if (clear([sideMiddle(source, side), this.point(a)], obstacles)) {
        costs.set(a, 0); origins.set(a, side)
      }
      if (clear([this.point(b), this.ports.get(target)?.get(side) ?? sideMiddle(target, side)], obstacles)) goals.set(b, side)
    }
    // Admissible Manhattan distance to the closest goal keeps corridor search local.
    const goalPoints = [...goals.keys()].map(id => this.point(id))
    const heuristic = (id: number): number => Math.min(...goalPoints.map(p => distance(this.point(id), p)))
    for (const id of origins.keys()) queue.push({ id, cost: heuristic(id) })
    for (let item = queue.pop(); item; item = queue.pop()) {
      const { id } = item, cost = costs.get(id)!
      if (item.cost !== cost + heuristic(id)) continue
      const to = goals.get(id)
      if (to) {
        const points = [this.point(id)]
        let cursor = id
        while (prev.has(cursor)) { cursor = prev.get(cursor)!; points.push(this.point(cursor)) }
        const from = origins.get(cursor)!
        points.reverse()
        const end = this.ports.get(target)?.get(to) ?? sideMiddle(target, to)
        const anchors = { ...edgeAnchors(source, target, from, to), targetX: end.x, targetY: end.y }
        return makeRoute(anchors, [sideMiddle(source, from), ...points, end])
      }
      for (const next of this.neighbors(id)) {
        const value = cost + distance(this.point(id), this.point(next))
        if (value >= (costs.get(next) ?? Infinity)) continue
        costs.set(next, value); prev.set(next, id); queue.push({ id: next, cost: value + heuristic(next) })
      }
    }
  }
}

function chooseRoute(source: NodeRect, target: NodeRect, rects: readonly NodeRect[], corridors: () => Corridors, ports: Ports): EdgeRoute {
  const anchors = (from: Position, to = opposite(from)): EdgeAnchors => {
    const a = edgeAnchors(source, target, from, to), end = ports.get(target)?.get(to)
    return end ? { ...a, targetX: end.x, targetY: end.y } : a
  }
  // Endpoint interiors are also protected. Other cards have the prototype's 12px clearance.
  const obstacles = rects.map(r => r === source || r === target ? r : inflate(r))
  const dx = target.x + target.width / 2 - source.x - source.width / 2
  const dy = target.y + target.height / 2 - source.y - source.height / 2
  const h = dx >= 0 ? Position.Right : Position.Left, v = dy >= 0 ? Position.Bottom : Position.Top
  const preferred = facingSide(source, target)
  const order = horizontal(preferred) ? [h, v] : [v, h]
  const accept = (a: EdgeAnchors, points: RoutePoint[]): EdgeRoute | undefined => {
    const route = makeRoute(a, points)
    return clear(route.points, obstacles) ? route : undefined
  }
  // Straight connections use the target side centre when it lies in the overlapping span.
  // Keeping a fixed target port makes same-side inputs converge without moving their arrow.
  for (const from of order) {
    const a = anchors(from)
    if (horizontal(from) && a.targetY >= source.y && a.targetY <= source.y + source.height) a.sourceY = a.targetY
    else if (!horizontal(from) && a.targetX >= source.x && a.targetX <= source.x + source.width) a.sourceX = a.targetX
    else continue
    const start = { x: a.sourceX, y: a.sourceY }, end = { x: a.targetX, y: a.targetY }
    if ((end.x - start.x) * outward[from].x + (end.y - start.y) * outward[from].y <= 0) continue
    const route = accept(a, [start, end])
    if (route) return route
  }
  for (const toDirection of order) {
    const from = horizontal(toDirection) ? v : h, to = opposite(toDirection)
    const a = anchors(from, to)
    const start = { x: a.sourceX, y: a.sourceY }, end = { x: a.targetX, y: a.targetY }
    const corner = horizontal(from) ? { x: end.x, y: start.y } : { x: start.x, y: end.y }
    const d = outward[from], q = outward[to]
    if ((corner.x - start.x) * d.x + (corner.y - start.y) * d.y < CLEARANCE
      || (corner.x - end.x) * q.x + (corner.y - end.y) * q.y < CLEARANCE) continue
    const route = accept(a, [start, corner, end])
    if (route) return route
  }
  for (const from of order) {
    const a = anchors(from), hor = horizontal(from)
    const start = { x: a.sourceX, y: a.sourceY }, end = { x: a.targetX, y: a.targetY }
    const lo = hor ? Math.min(start.x, end.x) : Math.min(start.y, end.y)
    const hi = hor ? Math.max(start.x, end.x) : Math.max(start.y, end.y)
    const middle = (lo + hi) / 2
    const lanes = [...new Set([middle, ...obstacles.flatMap(r => hor ? [r.x, r.x + r.width] : [r.y, r.y + r.height])])]
      .filter(n => n > lo && n < hi).sort((a, b) => Math.abs(a - middle) - Math.abs(b - middle))
    for (const lane of lanes) {
      const route = accept(a, [start, hor ? { x: lane, y: start.y } : { x: start.x, y: lane },
        hor ? { x: lane, y: end.y } : { x: end.x, y: lane }, end])
      if (route) return route
    }
  }
  const detour = corridors().route(source, target, obstacles)
  if (detour) return detour
  // Overlapping/touching cards may have no accessible port (explicitly outside the guarantee).
  const a = edgeAnchors(source, target), start = { x: a.sourceX, y: a.sourceY }, end = { x: a.targetX, y: a.targetY }
  return makeRoute(a, [start, { x: (start.x + end.x) / 2, y: start.y }, { x: (start.x + end.x) / 2, y: end.y }, end])
}

/** Standalone and canvas routes use the same candidate selection. */
export function routeEdge(source: NodeRect, target: NodeRect, obstacles: readonly NodeRect[] = []): EdgeRoute {
  const rects = [...new Set([source, target, ...obstacles])]
  const ports: Ports = new Map([[target, targetPorts(target, [source])]])
  return chooseRoute(source, target, rects, () => new Corridors(rects, ports), ports)
}

export interface RoutedEdge extends EdgeRoute { drawArrow: boolean }

/** Same target/side paths end at one port and share their final segment and arrow. */
export function routeEdges(
  edges: readonly { id: string; source: string; target: string }[],
  nodes: ReadonlyMap<string, NodeRect>,
): Map<string, RoutedEdge> {
  const result = new Map<string, RoutedEdge>(), arrows = new Set<string>()
  const rects = [...nodes.values()]
  const incoming = new Map<NodeRect, NodeRect[]>()
  for (const edge of edges) {
    const source = nodes.get(edge.source), target = nodes.get(edge.target)
    if (source && target && edge.source !== edge.target) incoming.set(target, [...incoming.get(target) ?? [], source])
  }
  const ports: Ports = new Map([...incoming].map(([target, sources]) => [target, targetPorts(target, sources)]))
  let grid: Corridors | undefined
  for (const edge of edges) {
    const source = nodes.get(edge.source), target = nodes.get(edge.target)
    if (!source || !target || edge.source === edge.target) continue
    const route = chooseRoute(source, target, rects, () => grid ??= new Corridors(rects, ports), ports)
    const key = JSON.stringify([edge.target, route.anchors.targetPosition])
    result.set(edge.id, { ...route, drawArrow: !arrows.has(key) })
    arrows.add(key)
  }
  return result
}

/** The path stops at the arrow base; its label is at half the travelled length. */
export function routeDrawing(route: EdgeRoute, arrowLength = 9): { path: string; middle: RoutePoint } {
  const points = route.points.map(p => ({ ...p }))
  const end = points[points.length - 1]!
  const direction = outward[route.anchors.targetPosition]
  const previous = points.at(-2)
  const trim = previous ? Math.min(Math.max(0, arrowLength), distance(previous, end)) : 0
  end.x += direction.x * trim
  end.y += direction.y * trim
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
