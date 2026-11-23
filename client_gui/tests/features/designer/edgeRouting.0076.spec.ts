import { describe, expect, it } from 'vitest'
import { Position } from '@vue-flow/core'
import { routeEdge, routeEdges, routeDrawing, segmentCrossesRect, type RouteKind, type RoutedEdge } from '@/features/designer/center/canvas/edgeRouting'
import type { NodeRect } from '@/features/designer/center/canvas/edgeGeometry'

// 0076 routing v2 = the approved MirageGlass prototype v2 (straight → L → two bends → detour).
// The shape counts below are the ones the prototype showed for the same scenes.
const rect = (x: number, y: number, width = 176, height = 108): NodeRect => ({ x, y, width, height })
type Edge = { id: string; source: string; target: string }
const edgesOf = (pairs: string[][]): Edge[] => pairs.map(([source, target], i) => ({ id: `e${i}`, source: source!, target: target! }))

/** Every line: orthogonal, ends on its anchors, passes through no card; one endpoint and one arrow per target side. */
function verify(nodes: Map<string, NodeRect>, edges: Edge[]): Map<string, RoutedEdge> {
  const routes = routeEdges(edges, nodes)
  expect(routes.size).toBe(edges.length)
  const sides = new Map<string, RoutedEdge[]>()
  for (const e of edges) {
    const r = routes.get(e.id)!
    expect(r.points[0]).toEqual({ x: r.anchors.sourceX, y: r.anchors.sourceY })
    expect(r.points.at(-1)).toEqual({ x: r.anchors.targetX, y: r.anchors.targetY })
    for (let i = 1; i < r.points.length; i++) {
      const a = r.points[i - 1]!, b = r.points[i]!
      expect(a.x === b.x || a.y === b.y).toBe(true)
      for (const [id, node] of nodes) {
        expect(segmentCrossesRect(a, b, node), `${e.source}->${e.target} crosses ${id}: ${JSON.stringify(r.points)}`).toBe(false)
      }
    }
    const key = `${e.target}:${r.anchors.targetPosition}`
    sides.set(key, [...sides.get(key) ?? [], r])
  }
  for (const group of sides.values()) {
    expect(new Set(group.map(r => JSON.stringify(r.points.at(-1)))).size).toBe(1)
    expect(group.filter(r => r.drawArrow)).toHaveLength(1)
  }
  return routes
}
function kinds(routes: Map<string, RoutedEdge>): Partial<Record<RouteKind, number>> {
  const out: Partial<Record<RouteKind, number>> = {}
  for (const r of routes.values()) out[r.kind] = (out[r.kind] ?? 0) + 1
  return out
}

const imageEdges = edgesOf([['req', 'srv'], ['req', 'c345'], ['req', 'c12'], ['srv', 'scr'], ['c345', 'scr'], ['c12', 'scr']])
const image = (reqY: number, rows: [number, number, number]) => new Map([
  ['req', rect(0, reqY)], ['srv', rect(260, rows[0])], ['c345', rect(260, rows[1])],
  ['c12', rect(260, rows[2])], ['scr', rect(520, rows[1])],
])

describe('0076 routing v2: the prototype scenes', () => {
  it('image 2 (requirement moved up): 2 straight, 4 L; the line to 1·2 no longer runs behind 3·4·5', () => {
    const routes = verify(image(0, [0, 139, 279]), imageEdges)
    expect(kinds(routes)).toEqual({ straight: 2, l: 4 })
    const toC12 = routes.get('e2')!
    expect([toC12.anchors.sourcePosition, toC12.anchors.targetPosition]).toEqual([Position.Bottom, Position.Left])
    expect(toC12.points).toEqual([{ x: 88, y: 108 }, { x: 88, y: 333 }, { x: 260, y: 333 }])
    // The requirement's two downward lines share one stem.
    expect(routes.get('e1')!.points[0]).toEqual(toC12.points[0])
  })
  it('image 1 (auto layout): 2 straight, 4 L', () => {
    expect(kinds(verify(image(140, [0, 140, 280]), imageEdges))).toEqual({ straight: 2, l: 4 })
  })
  it('mixed vertical scene: 3 L, 2 two-bend, 2 detours', () => {
    const routes = verify(new Map([['a', rect(0, 0)], ['b', rect(0, 300)], ['c', rect(260, 150)],
      ['d', rect(520, 0)], ['e', rect(520, 300)], ['f', rect(260, 420)]]),
    edgesOf([['a', 'e'], ['b', 'd'], ['a', 'c'], ['c', 'e'], ['b', 'f'], ['a', 'f'], ['d', 'b']]))
    expect(kinds(routes)).toEqual({ l: 3, twoBend: 2, detour: 2 })
  })
  it('ten inputs past other cards: 5 L, 1 two-bend, 4 detours', () => {
    const nodes = new Map([['t', rect(600, 620)]])
    for (let i = 0; i < 10; i++) nodes.set(`s${i}`, rect(0, i * 140))
    for (let i = 0; i < 4; i++) nodes.set(`o${i}`, rect(300, 120 + i * 300))
    const routes = verify(nodes, edgesOf(Array.from({ length: 10 }, (_, i) => [`s${i}`, 't'])))
    expect(kinds(routes)).toEqual({ l: 5, twoBend: 1, detour: 4 })
  })
  it('100 lines across a middle column: every line detours, none crosses a card, also while a source moves', () => {
    const nodes = new Map<string, NodeRect>()
    for (let i = 0; i < 10; i++) {
      nodes.set(`s${i}`, rect(0, i * 150)); nodes.set(`m${i}`, rect(330, i * 150 + 20)); nodes.set(`t${i}`, rect(660, i * 150))
    }
    const edges = edgesOf(Array.from({ length: 100 }, (_, i) => [`s${Math.floor(i / 10)}`, `t${i % 10}`]))
    expect(kinds(verify(nodes, edges))).toEqual({ detour: 100 })
    nodes.set('s0', rect(-30, -20))
    verify(nodes, edges)
  })
})

describe('0076 routing v2: candidate order', () => {
  it('aligned cards keep one straight line between the side centres', () => {
    const r = routeEdge(rect(0, 0), rect(400, 0))
    expect(r.kind).toBe('straight')
    expect(r.points).toEqual([{ x: 176, y: 54 }, { x: 400, y: 54 }])
  })
  it('diagonal cards use an L that enters along the larger separation', () => {
    const r = routeEdge(rect(0, 0), rect(600, 300))
    expect(r.kind).toBe('l')
    expect([r.anchors.sourcePosition, r.anchors.targetPosition]).toEqual([Position.Bottom, Position.Left])
    expect(r.points).toEqual([{ x: 88, y: 108 }, { x: 88, y: 354 }, { x: 600, y: 354 }])
  })
  it('uses the other L when the preferred one is blocked', () => {
    const r = routeEdge(rect(0, 0), rect(700, 300), [rect(0, 150)])
    expect(r.kind).toBe('l')
    expect([r.anchors.sourcePosition, r.anchors.targetPosition]).toEqual([Position.Right, Position.Top])
  })
  it('falls back to two bends when no L fits, as before 0076', () => {
    const r = routeEdge(rect(0, 0), rect(40, 300))
    expect(r.kind).toBe('twoBend')
    expect(r.points).toEqual([{ x: 88, y: 108 }, { x: 88, y: 204 }, { x: 128, y: 204 }, { x: 128, y: 300 }])
  })
  it('detours around a wall that blocks every direct shape', () => {
    const wall = rect(300, -100, 100, 400)
    const r = verify(new Map([['s', rect(0, 0)], ['t', rect(700, 0)], ['wall', wall]]), edgesOf([['s', 't']])).get('e0')!
    expect(r.kind).toBe('detour')
    expect(r.points.some(p => p.y < wall.y || p.y > wall.y + wall.height)).toBe(true)
    expect(routeEdge(rect(0, 0), rect(700, 0), [wall]).points).toEqual(r.points)
  })
  it('ignores self lines and missing cards', () => {
    expect(routeEdges(edgesOf([['req', 'req'], ['missing', 'req']]), image(0, [0, 139, 279])).size).toBe(0)
  })
  it('draws to the arrow base and puts the label on the line', () => {
    const r = routeEdge(rect(0, 0), rect(600, 300))
    const { path, middle } = routeDrawing(r)
    expect(path).toBe('M88,108 L88,354 L591,354')
    expect(middle.y === 354 || middle.x === 88).toBe(true)
  })
  it('passes through no card in 1000 seeded scenes whose cards are at least 40px apart', () => {
    let seed = 76
    const random = () => { seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0; return seed / 4294967296 }
    for (let scene = 0; scene < 1000; scene++) {
      const nodes = new Map<string, NodeRect>()
      while (nodes.size < 8) {
        const r = rect(Math.floor(random() * 1800) - 900, Math.floor(random() * 1400) - 700)
        if ([...nodes.values()].some(o => !(r.x >= o.x + o.width + 40 || o.x >= r.x + r.width + 40
          || r.y >= o.y + o.height + 40 || o.y >= r.y + r.height + 40))) continue
        nodes.set(String(nodes.size), r)
      }
      verify(nodes, edgesOf(Array.from({ length: 8 }, (_, i) => [String(i), String((i + 3) % 8)])))
    }
  }, 60000)
})
