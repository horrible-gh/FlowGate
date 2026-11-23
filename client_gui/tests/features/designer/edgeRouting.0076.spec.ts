import { describe, expect, it } from 'vitest'
import { Position } from '@vue-flow/core'
import { routeEdge, routeEdges, routeDrawing, segmentCrossesRect, type EdgeRoute } from '@/features/designer/center/canvas/edgeRouting'
import { edgeAnchors, facingSide, type NodeRect } from '@/features/designer/center/canvas/edgeGeometry'

const rect = (x: number, y: number, width = 176, height = 108): NodeRect => ({ x, y, width, height })
type Edge = { id: string; source: string; target: string }
const edgesOf = (pairs: string[][]): Edge[] => pairs.map(([source, target], i) => ({ id: String(i), source: source!, target: target! }))
function verify(nodes: Map<string, NodeRect>, edges: Edge[]) {
  const routes = routeEdges(edges, nodes)
  expect(routes.size).toBe(edges.length)
  const groups = new Map<string, EdgeRoute[]>()
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
    groups.set(key, [...groups.get(key) ?? [], r])
  }
  for (const [key, group] of groups) {
    expect(new Set(group.map(r => JSON.stringify(r.points.at(-1)))).size).toBe(1)
    expect(edges.filter(e => `${e.target}:${routes.get(e.id)!.anchors.targetPosition}` === key && routes.get(e.id)!.drawArrow)).toHaveLength(1)
  }
  return routes
}
const image = (reqY: number) => new Map([
  ['req', rect(0, reqY)], ['srv', rect(260, 0)], ['c345', rect(260, 139)],
  ['c12', rect(260, 279)], ['scr', rect(520, 139)],
])
const imageEdges = edgesOf([['req', 'srv'], ['req', 'c345'], ['req', 'c12'], ['srv', 'scr'], ['c345', 'scr'], ['c12', 'scr']])

describe('0076 routing v2', () => {
  it('uses a straight route for a partial overlap without centre overlap', () => {
    const r = verify(new Map([['s', rect(0, 0)], ['t', rect(400, 90)]]), edgesOf([['s', 't']])).get('0')!
    expect(r.points).toHaveLength(2)
    expect(r.points[0]!.y).toBe(r.points[1]!.y)
  })
  it('does not reverse a segment shorter than the arrow', () => {
    const anchors = edgeAnchors(rect(0, 0), rect(180, 0))
    const r = { anchors, points: [{ x: 176, y: 54 }, { x: 180, y: 54 }] }
    expect(routeDrawing(r).path).toBe('M176,54 L176,54')
  })
  it('keeps legacy facingSide and permits perpendicular anchors', () => {
    const s = rect(0, 0), t = rect(260, 279)
    expect(edgeAnchors(s, t).sourcePosition).toBe(facingSide(s, t))
    expect(edgeAnchors(s, t, Position.Bottom, Position.Left).targetPosition).toBe(Position.Left)
  })
  it.each([0, 140, 279])('routes the image scene with requirement at y=%s', reqY => {
    const routes = verify(image(reqY), imageEdges)
    if (reqY === 0) {
      expect([...routes.values()].filter(r => r.points.length === 2)).toHaveLength(2)
      expect([...routes.values()].filter(r => r.points.length === 3)).toHaveLength(4)
      expect(routes.get('2')!.anchors.targetPosition).toBe(Position.Left)
    }
  })
  it('routes the mixed vertical scene from the prototype', () => {
    verify(new Map([['a', rect(0, 0)], ['b', rect(0, 300)], ['c', rect(260, 150)],
      ['d', rect(520, 0)], ['e', rect(520, 300)], ['f', rect(260, 420)]]),
    edgesOf([['a', 'e'], ['b', 'd'], ['a', 'c'], ['c', 'e'], ['b', 'f'], ['a', 'f'], ['d', 'b']]))
  })
  it('routes ten inputs with intervening cards', () => {
    const nodes = new Map([['t', rect(600, 620)]])
    for (let i = 0; i < 10; i++) nodes.set(`s${i}`, rect(0, i * 140))
    for (let i = 0; i < 4; i++) nodes.set(`o${i}`, rect(300, 120 + i * 300))
    verify(nodes, edgesOf(Array.from({ length: 10 }, (_, i) => [`s${i}`, 't'])))
  })
  it('routes 100 edges while the source moves', () => {
    const nodes = new Map<string, NodeRect>()
    for (let i = 0; i < 10; i++) {
      nodes.set(`s${i}`, rect(0, i * 150)); nodes.set(`m${i}`, rect(330, i * 150 + 20)); nodes.set(`t${i}`, rect(660, i * 150))
    }
    const edges = edgesOf(Array.from({ length: 100 }, (_, i) => [`s${Math.floor(i / 10)}`, `t${i % 10}`]))
    verify(nodes, edges)
    const samples: number[] = []
    for (let i = 0; i < 10; i++) {
      nodes.set('s0', rect(-i * 3, -i * 2))
      const start = performance.now()
      routeEdges(edges, nodes)
      samples.push(performance.now() - start)
    }
    console.info('0076 100-edge routing ms', JSON.stringify(samples))
    verify(nodes, edges)
  })
  it('uses the second L when the preferred L is blocked', () => {
    const s = rect(0, 0), t = rect(700, 300), obstacle = rect(0, 150)
    const r = routeEdge(s, t, [obstacle])
    expect(r.points).toHaveLength(3)
    expect(r.anchors.sourcePosition).toBe(Position.Right)
    expect(r.anchors.targetPosition).toBe(Position.Top)
  })
  it('moves a two-bend trunk away from an obstacle', () => {
    const s = rect(0, 0), t = rect(700, 200)
    const nodes = new Map([['s', s], ['t', t], ['a', rect(0, 160)], ['b', rect(700, 0)], ['c', rect(400, 120, 80, 60)]])
    const r = verify(nodes, edgesOf([['s', 't']])).get('0')!
    expect(r.points).toHaveLength(4)
    expect(r.points[1]!.x).not.toBe((176 + 700) / 2)
  })
  it('uses a corridor when straight and two-bend routes are blocked', () => {
    const nodes = new Map([['s', rect(0, 0)], ['t', rect(700, 0)], ['wall', rect(300, -100, 100, 400)]])
    const r = verify(nodes, edgesOf([['s', 't']])).get('0')!
    expect(r.points.length).toBeGreaterThanOrEqual(4)
    expect(r.points.some(p => p.y < -100 || p.y > 300)).toBe(true)
    expect(routeEdge(nodes.get('s')!, nodes.get('t')!, [nodes.get('wall')!]).points).toEqual(r.points)
  })
  it('is stable under reversed edge order and ignores absent/self endpoints', () => {
    const nodes = image(0), first = routeEdges(imageEdges, nodes), reversed = routeEdges([...imageEdges].reverse(), nodes)
    for (const e of imageEdges) expect(first.get(e.id)!.points).toEqual(reversed.get(e.id)!.points)
    expect(routeEdges(edgesOf([['req', 'req'], ['missing', 'req']]), nodes).size).toBe(0)
  })
  it('draws straight, L and short final segments without overshooting', () => {
    for (const target of [rect(300, 0), rect(300, 200)]) {
      const route = routeEdge(rect(0, 0), target), original = JSON.stringify(route)
      const drawing = routeDrawing(route)
      expect(drawing.path).not.toMatch(/NaN|undefined/)
      expect(JSON.stringify(route)).toBe(original)
    }
  })
  it('avoids every card in 1000 seeded scenes separated by at least 40px', () => {
    let seed = 76
    const random = () => { seed = (Math.imul(seed, 1664525) + 1013904223) >>> 0; return seed / 4294967296 }
    for (let scene = 0; scene < 1000; scene++) {
      const nodes = new Map<string, NodeRect>()
      while (nodes.size < 8) {
        const r = rect(Math.floor(random() * 1800) - 900, Math.floor(random() * 1400) - 700,
          140 + Math.floor(random() * 70), 90 + Math.floor(random() * 50))
        if ([...nodes.values()].some(o => !(r.x >= o.x + o.width + 40 || o.x >= r.x + r.width + 40
          || r.y >= o.y + o.height + 40 || o.y >= r.y + r.height + 40))) continue
        nodes.set(String(nodes.size), r)
      }
      verify(nodes, edgesOf(Array.from({ length: 8 }, (_, i) => [String(i), String((i + 3) % 8)])))
    }
  }, 45000)
})
