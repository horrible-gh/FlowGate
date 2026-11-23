import { Position } from '@vue-flow/core'
import { edgeAnchors, facingSide, sideMiddle, type EdgeAnchors, type NodeRect } from './edgeGeometry'

// 0076 routing v2 (the approved prototype "직선 → ㄴ자 → 두 번 꺾기 → 돌아가기"): every line still
// leaves and enters at a side centre, any of the four sides. Per line the first candidate that does
// not pass through another card wins, in this order:
//   1. straight, when the facing sides line up;
//   2. L (one bend), entering the side along the cards' larger separation first;
//   3. two bends between facing sides (the shared-trunk route of the inputs of one card), then the
//      two-bend route on the other axis;
//   4. a detour through the free corridors around the cards.
// Inputs entering the same side of a card meet at its side centre and draw one arrow.

export interface RoutePoint { x: number; y: number }
export interface EdgeRoute { anchors: EdgeAnchors; points: RoutePoint[] }
export type RouteKind = 'straight' | 'l' | 'twoBend' | 'detour'
export interface RoutedEdge extends EdgeRoute { drawArrow: boolean; kind: RouteKind }

const outward: Record<Position, RoutePoint> = {
  [Position.Left]: { x: -1, y: 0 }, [Position.Right]: { x: 1, y: 0 },
  [Position.Top]: { x: 0, y: -1 }, [Position.Bottom]: { x: 0, y: 1 },
}
const OPPOSITE: Record<Position, Position> = {
  [Position.Left]: Position.Right, [Position.Right]: Position.Left,
  [Position.Top]: Position.Bottom, [Position.Bottom]: Position.Top,
}
const SIDES = [Position.Right, Position.Bottom, Position.Left, Position.Top]
const isVertical = (side: Position): boolean => side === Position.Top || side === Position.Bottom

/** Minimum stub before a bend and the detour grid's distance from every card. */
const MARGIN = 20
/** The final segment of an L also holds the arrow head. */
const ARROW = 9
/** Distance a line keeps from cards other than its own two. */
const CLEARANCE = 12
/** Detour cost of one bend. */
const BEND = 24

/** Strict interior intersection: travelling along a card's edge is not crossing it. */
export function segmentCrossesRect(a: RoutePoint, b: RoutePoint, r: NodeRect): boolean {
  if (a.x === b.x) return a.x > r.x && a.x < r.x + r.width
    && Math.max(a.y, b.y) > r.y && Math.min(a.y, b.y) < r.y + r.height
  if (a.y === b.y) return a.y > r.y && a.y < r.y + r.height
    && Math.max(a.x, b.x) > r.x && Math.min(a.x, b.x) < r.x + r.width
  return true // router only emits orthogonal segments
}

type Edge = { id: string; source: string; target: string }
interface Candidate { kind: RouteKind; points: RoutePoint[]; from: Position; to: Position }
interface Placed { source: string; target: string; a: RoutePoint; b: RoutePoint }

function centreDelta(s: NodeRect, t: NodeRect): { dx: number; dy: number } {
  return { dx: t.x + t.width / 2 - (s.x + s.width / 2), dy: t.y + t.height / 2 - (s.y + s.height / 2) }
}

/** The pre-0076 route: facing sides, two bends, inputs of one target side share the nearest trunk. */
function facingRoutes(edges: readonly Edge[], nodes: ReadonlyMap<string, NodeRect>): Map<string, Candidate> {
  const trunks = new Map<string, number>()
  const anchors = new Map<string, EdgeAnchors>()
  for (const edge of edges) {
    const s = nodes.get(edge.source), t = nodes.get(edge.target)
    if (!s || !t || edge.source === edge.target) continue
    const a = edgeAnchors(s, t), d = outward[a.targetPosition]
    const key = `${edge.target}:${a.targetPosition}`
    const gap = (a.sourceX - a.targetX) * d.x + (a.sourceY - a.targetY) * d.y
    trunks.set(key, Math.min(trunks.get(key) ?? Infinity, gap / 2))
    anchors.set(edge.id, a)
  }
  const result = new Map<string, Candidate>()
  for (const edge of edges) {
    const a = anchors.get(edge.id)
    if (!a) continue
    const d = outward[a.targetPosition], horizontal = d.x !== 0
    const start = { x: a.sourceX, y: a.sourceY }, end = { x: a.targetX, y: a.targetY }
    const offset = trunks.get(`${edge.target}:${a.targetPosition}`)!
    const gap = (start.x - end.x) * d.x + (start.y - end.y) * d.y
    const clearance = gap > 0 ? Math.min(offset, gap / 2) : offset
    const trunk = horizontal ? end.x + d.x * clearance : end.y + d.y * clearance
    result.set(edge.id, { kind: 'twoBend', from: a.sourcePosition, to: a.targetPosition, points: [start,
      horizontal ? { x: trunk, y: start.y } : { x: start.x, y: trunk },
      horizontal ? { x: trunk, y: end.y } : { x: end.x, y: trunk }, end] })
  }
  return result
}

function lShape(s: NodeRect, t: NodeRect, from: Position, to: Position): RoutePoint[] | null {
  if (isVertical(from) === isVertical(to)) return null
  const a = sideMiddle(s, from), b = sideMiddle(t, to)
  const corner = isVertical(from) ? { x: a.x, y: b.y } : { x: b.x, y: a.y }
  const o = outward[from], q = outward[to]
  if ((corner.x - a.x) * o.x + (corner.y - a.y) * o.y < MARGIN) return null
  if ((corner.x - b.x) * q.x + (corner.y - b.y) * q.y < MARGIN + ARROW) return null
  return [a, corner, b]
}

function twoBend(s: NodeRect, t: NodeRect, horizontal: boolean): Candidate | null {
  const { dx, dy } = centreDelta(s, t)
  const from = horizontal ? (dx >= 0 ? Position.Right : Position.Left) : (dy >= 0 ? Position.Bottom : Position.Top)
  const to = OPPOSITE[from], a = sideMiddle(s, from), b = sideMiddle(t, to)
  const gap = horizontal ? (b.x - a.x) * outward[from].x : (b.y - a.y) * outward[from].y
  if (gap < 2 * MARGIN) return null
  if (horizontal) {
    const x = (a.x + b.x) / 2
    return { kind: 'twoBend', from, to, points: a.y === b.y ? [a, b] : [a, { x, y: a.y }, { x, y: b.y }, b] }
  }
  const y = (a.y + b.y) / 2
  return { kind: 'twoBend', from, to, points: a.x === b.x ? [a, b] : [a, { x: a.x, y }, { x: b.x, y }, b] }
}

function blocked(points: readonly RoutePoint[], nodes: ReadonlyMap<string, NodeRect>, edge: Edge): boolean {
  for (let i = 0; i + 1 < points.length; i++) {
    for (const [id, r] of nodes) {
      const g = id === edge.source || id === edge.target ? 0 : CLEARANCE
      if (segmentCrossesRect(points[i]!, points[i + 1]!, { x: r.x - g, y: r.y - g, width: r.width + 2 * g, height: r.height + 2 * g })) return true
    }
  }
  return false
}

/** Two segments lying on the same line and sharing more than a pixel. */
function overlaps(a: RoutePoint, b: RoutePoint, c: RoutePoint, d: RoutePoint): boolean {
  if (a.x === b.x && c.x === d.x && a.x === c.x) {
    return Math.min(Math.max(a.y, b.y), Math.max(c.y, d.y)) - Math.max(Math.min(a.y, b.y), Math.min(c.y, d.y)) > 1
  }
  if (a.y === b.y && c.y === d.y && a.y === c.y) {
    return Math.min(Math.max(a.x, b.x), Math.max(c.x, d.x)) - Math.max(Math.min(a.x, b.x), Math.min(c.x, d.x)) > 1
  }
  return false
}

function simplify(points: readonly RoutePoint[]): RoutePoint[] {
  const out: RoutePoint[] = [points[0]!]
  for (let i = 1; i < points.length; i++) {
    const a = out[out.length - 1]!, b = points[i]!
    if (a.x === b.x && a.y === b.y) continue
    const z = out[out.length - 2]
    if (z && ((z.x === a.x && a.x === b.x) || (z.y === a.y && a.y === b.y))) { out[out.length - 1] = b; continue }
    out.push(b)
  }
  return out
}

/** A binary min-heap of [priority, cost, state]. */
class Heap {
  private items: [number, number, number][] = []
  get size(): number { return this.items.length }
  push(v: [number, number, number]): void {
    const a = this.items
    a.push(v)
    let i = a.length - 1
    while (i) {
      const p = (i - 1) >> 1
      if (a[p]![0] <= a[i]![0]) break;
      [a[p], a[i]] = [a[i]!, a[p]!]
      i = p
    }
  }
  pop(): [number, number, number] {
    const a = this.items, top = a[0]!, last = a.pop()!
    if (a.length) {
      a[0] = last
      let i = 0
      for (;;) {
        const l = 2 * i + 1, r = l + 1
        let m = i
        if (l < a.length && a[l]![0] < a[m]![0]) m = l
        if (r < a.length && a[r]![0] < a[m]![0]) m = r
        if (m === i) break;
        [a[m], a[i]] = [a[i]!, a[m]!]
        i = m
      }
    }
    return top
  }
}

/** The corridor grid around all cards (kept MARGIN away from each), built once per canvas update. */
class Corridors {
  private xs: number[]
  private ys: number[]
  private xi: Map<number, number>
  private yi: Map<number, number>
  private free: Uint8Array
  /** 0 = not yet checked, 1 = open, 2 = blocked. */
  private steps: Uint8Array
  /** A* state per (cell, heading); the last slot is the goal. */
  private goalKey: number
  private best: Float64Array
  private stamp: Uint32Array
  private prev: Int32Array
  private run = 0
  /** Grid lines running along a card's margin. */
  private bx = new Set<number>()
  private by = new Set<number>()
  private hugX: Uint8Array
  private hugY: Uint8Array
  constructor(private rects: readonly NodeRect[]) {
    const xs = new Set<number>(), ys = new Set<number>()
    let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity
    for (const r of rects) {
      for (const v of [r.x - MARGIN, r.x + r.width / 2, r.x + r.width + MARGIN]) xs.add(v)
      for (const v of [r.y - MARGIN, r.y + r.height / 2, r.y + r.height + MARGIN]) ys.add(v)
      minX = Math.min(minX, r.x); maxX = Math.max(maxX, r.x + r.width)
      minY = Math.min(minY, r.y); maxY = Math.max(maxY, r.y + r.height)
      this.bx.add(r.x - MARGIN); this.bx.add(r.x + r.width + MARGIN)
      this.by.add(r.y - MARGIN); this.by.add(r.y + r.height + MARGIN)
    }
    xs.add(minX - 3 * MARGIN); xs.add(maxX + 3 * MARGIN); ys.add(minY - 3 * MARGIN); ys.add(maxY + 3 * MARGIN)
    const withMiddles = (s: Set<number>): number[] => {
      const v = [...s].sort((a, b) => a - b)
      for (let i = 0; i + 1 < v.length; i++) s.add((v[i]! + v[i + 1]!) / 2)
      return [...s].sort((a, b) => a - b)
    }
    this.xs = withMiddles(xs)
    this.ys = withMiddles(ys)
    this.xi = new Map(this.xs.map((v, i) => [v, i]))
    this.yi = new Map(this.ys.map((v, i) => [v, i]))
    const nx = this.xs.length
    this.free = new Uint8Array(nx * this.ys.length)
    for (let j = 0; j < this.ys.length; j++) {
      for (let i = 0; i < nx; i++) this.free[j * nx + i] = this.inside(this.xs[i]!, this.ys[j]!) ? 0 : 1
    }
    this.steps = new Uint8Array(nx * this.ys.length * 4)
    this.hugX = Uint8Array.from(this.xs, x => this.bx.has(x) ? 1 : 0)
    this.hugY = Uint8Array.from(this.ys, y => this.by.has(y) ? 1 : 0)
    this.goalKey = nx * this.ys.length * 4
    this.best = new Float64Array(this.goalKey + 1)
    this.stamp = new Uint32Array(this.goalKey + 1)
    this.prev = new Int32Array(this.goalKey + 1)
  }
  /** Whether the grid step from cell (i, j) to (k, l) stays clear of every card; cached per cell and direction. */
  private open(node: number, direction: number, i: number, j: number, k: number, l: number): boolean {
    const slot = node * 4 + direction, known = this.steps[slot]
    if (known) return known === 1
    const nx = this.xs.length
    const ok = k >= 0 && l >= 0 && k < nx && l < this.ys.length && this.free[l * nx + k] === 1
      && !this.inside((this.xs[i]! + this.xs[k]!) / 2, (this.ys[j]! + this.ys[l]!) / 2)
    this.steps[slot] = ok ? 1 : 2
    return ok
  }
  private inside(x: number, y: number): boolean {
    return this.rects.some(r => x > r.x - MARGIN && x < r.x + r.width + MARGIN && y > r.y - MARGIN && y < r.y + r.height + MARGIN)
  }
  private ports(r: NodeRect): { side: Position; p: RoutePoint; node: number }[] {
    const nx = this.xs.length, result = []
    for (const side of SIDES) {
      const p = sideMiddle(r, side), o = outward[side]
      const i = this.xi.get(p.x + o.x * MARGIN), j = this.yi.get(p.y + o.y * MARGIN)
      if (i !== undefined && j !== undefined && this.free[j * nx + i]) result.push({ side, p, node: j * nx + i })
    }
    return result
  }
  route(s: NodeRect, t: NodeRect): Candidate | null {
    const { xs, ys } = this, nx = xs.length, GOAL = this.goalKey
    const fromSide = facingSide(s, t), toSide = OPPOSITE[fromSide]
    const starts = this.ports(s)
    const goals = new Map(this.ports(t).map(g => [g.node, { ...g, penalty: g.side === toSide ? 0 : 3 }]))
    const goalXY = [...goals.keys()].map(k => [xs[k % nx]!, ys[Math.floor(k / nx)]!] as const)
    const h = (i: number, j: number): number => {
      let m = Infinity
      for (const [x, y] of goalXY) m = Math.min(m, Math.abs(xs[i]! - x) + Math.abs(ys[j]! - y))
      return m
    }
    // Search state lives in typed arrays reused across lines; `stamp` marks the entries of this search.
    const run = ++this.run, { best, stamp, prev } = this
    const cost = (key: number): number => stamp[key] === run ? best[key]! : Infinity
    const set = (key: number, g: number, from: number): void => { stamp[key] = run; best[key] = g; prev[key] = from }
    let goalSide: (typeof starts)[number] | undefined
    const heap = new Heap()
    starts.forEach((st, index) => {
      const i = st.node % nx, j = Math.floor(st.node / nx)
      const key = st.node * 4 + SIDES.indexOf(st.side), g = MARGIN + (st.side === fromSide ? 0 : 3)
      if (cost(key) > g) { set(key, g, -1 - index); heap.push([g + h(i, j), g, key]) }
    })
    let found = false
    while (heap.size) {
      const [, g, key] = heap.pop()
      if (key === GOAL) { found = true; break }
      if (g > cost(key)) continue
      const node = Math.floor(key / 4), d = key % 4, i = node % nx, j = Math.floor(node / nx)
      const goal = goals.get(node)
      if (goal && d !== SIDES.indexOf(goal.side)) {
        const gg = g + MARGIN + goal.penalty + (d === SIDES.indexOf(OPPOSITE[goal.side]) ? 0 : BEND)
        if (cost(GOAL) > gg) { set(GOAL, gg, key); goalSide = goal; heap.push([gg, gg, GOAL]) }
      }
      for (let nd = 0; nd < 4; nd++) {
        if (nd === (d + 2) % 4) continue
        const o = outward[SIDES[nd]!], k = i + o.x, l = j + o.y
        if (!this.open(node, nd, i, j, k, l)) continue
        const length = Math.abs(xs[k]! - xs[i]!) + Math.abs(ys[l]! - ys[j]!)
        // Running right along a card's margin line is allowed but slightly discouraged.
        const hugging = i === k ? this.hugX[i] === 1 : this.hugY[j] === 1
        const step = hugging ? length + length * 0.15 : length
        const ng = g + step + (nd === d ? 0 : BEND), nk = (l * nx + k) * 4 + nd
        if (ng < cost(nk)) { set(nk, ng, key); heap.push([ng + h(k, l), ng, nk]) }
      }
    }
    if (!found || !goalSide) return null
    const path: RoutePoint[] = []
    let key = prev[GOAL]!
    for (;;) {
      const n = Math.floor(key / 4)
      path.push({ x: xs[n % nx]!, y: ys[Math.floor(n / nx)]! })
      if (prev[key]! < 0) break
      key = prev[key]!
    }
    const start = starts[-1 - prev[key]!]!
    path.reverse()
    return { kind: 'detour', from: start.side, to: goalSide.side, points: simplify([start.p, ...path, goalSide.p]) }
  }
}

function toRoute(c: Candidate): EdgeRoute {
  const a = c.points[0]!, b = c.points[c.points.length - 1]!
  return { anchors: { sourceX: a.x, sourceY: a.y, sourcePosition: c.from, targetX: b.x, targetY: b.y, targetPosition: c.to }, points: c.points }
}

/** Route the whole canvas together; the order of `edges` decides which line keeps a shared lane first. */
export function routeEdges(edges: readonly Edge[], nodes: ReadonlyMap<string, NodeRect>): Map<string, RoutedEdge> {
  const facing = facingRoutes(edges, nodes)
  const placed: Placed[] = []
  const routes = new Map<string, Candidate>()
  let corridors: Corridors | undefined
  for (const edge of edges) {
    const s = nodes.get(edge.source), t = nodes.get(edge.target), current = facing.get(edge.id)
    if (!s || !t || !current) continue
    const dominant = facingSide(s, t), { dx, dy } = centreDelta(s, t)
    const h = dx >= 0 ? Position.Right : Position.Left, v = dy >= 0 ? Position.Bottom : Position.Top
    const first = current.points[0]!, last = current.points[3]!
    const straight = first.x === last.x || first.y === last.y
    const candidates: Candidate[] = []
    if (straight) candidates.push({ ...current, kind: 'straight', points: [first, last] })
    const lOrder: [Position, Position][] = isVertical(dominant) ? [[h, OPPOSITE[v]], [v, OPPOSITE[h]]] : [[v, OPPOSITE[h]], [h, OPPOSITE[v]]]
    for (const [from, to] of lOrder) {
      const points = lShape(s, t, from, to)
      if (points) candidates.push({ kind: 'l', from, to, points })
    }
    if (!straight) candidates.push(current)
    const other = twoBend(s, t, isVertical(dominant))
    if (other) candidates.push(other)
    let pick: Candidate | null = null, soft: Candidate | null = null
    for (const c of candidates) {
      if (blocked(c.points, nodes, edge)) continue
      // Prefer not to run on top of an unrelated line; lines of the same source or target may share.
      const foreign = c.points.some((a, i) => i > 0 && placed.some(z => z.source !== edge.source
        && z.target !== edge.target && overlaps(c.points[i - 1]!, a, z.a, z.b)))
      if (!foreign) { pick = c; break }
      soft ??= c
    }
    pick ??= soft
    if (!pick) {
      corridors ??= new Corridors([...nodes.values()])
      pick = corridors.route(s, t)
    }
    // Overlapping cards can leave no free corridor: fall back to the plain facing route.
    pick ??= { ...facingRoutes([edge], nodes).get(edge.id)!, kind: 'detour' }
    for (let i = 1; i < pick.points.length; i++) placed.push({ source: edge.source, target: edge.target, a: pick.points[i - 1]!, b: pick.points[i]! })
    routes.set(edge.id, pick)
  }
  const result = new Map<string, RoutedEdge>(), arrows = new Set<string>()
  for (const edge of edges) {
    const c = routes.get(edge.id)
    if (!c) continue
    const key = `${edge.target}:${c.to}`
    result.set(edge.id, { ...toRoute(c), kind: c.kind, drawArrow: !arrows.has(key) })
    arrows.add(key)
  }
  return result
}

/** One line on its own, with `obstacles` as the other cards it must not pass through. */
export function routeEdge(source: NodeRect, target: NodeRect, obstacles: readonly NodeRect[] = []): RoutedEdge {
  const nodes = new Map<string, NodeRect>([['s', source], ['t', target]])
  obstacles.forEach((r, i) => nodes.set(`o${i}`, r))
  return routeEdges([{ id: 'e', source: 's', target: 't' }], nodes).get('e')!
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
