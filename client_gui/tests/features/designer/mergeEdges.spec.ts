import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import type { GraphNode } from '@vue-flow/core'
import { waitingEdgeIds, progressedEdgeIds } from '@/features/designer/center/graph/edgeProgress'
import { toVueFlowEdges } from '@/features/designer/center/canvas/vueFlowMapping'
import { routeEdge, routeDrawing, segmentCrossesRect } from '@/features/designer/center/canvas/edgeRouting'
import { nodeRect, type NodeRect } from '@/features/designer/center/canvas/edgeGeometry'
import type { FlowGraph } from '@/features/designer/center/graph/flowGraph'
import FlowEdgePath from '@/features/designer/center/canvas/FlowEdgePath.vue'

const graph = (peer: string | null = 'in_progress', target = 'draft'): FlowGraph => ({
  flowId: 'test',
  nodes: [{ id: 'B1', state: 'done' }, { id: 'A2', state: peer }, { id: 'C', state: target }]
    .map(n => ({ ...n, kind: 'task', title: n.id, x: 0, y: 0 })),
  edges: ['B1', 'A2'].map(source => ({ id: source + '>C', source, target: 'C', edgeClass: 'execution', editable: true })),
})
const rect = (x: number, y: number): NodeRect => ({ x, y, width: 176, height: 108 })
const node = (id: string, x: number, y: number): GraphNode =>
  ({ id, computedPosition: { x, y, z: 0 }, dimensions: { width: 0, height: 0 } }) as GraphNode
const i18n = createI18n({ legacy: false, locale: 'en', messages: { en: {} }, missingWarn: false, fallbackWarn: false })

describe('arrived input waiting for a peer', () => {
  it.each(['draft', 'in_progress', 'review', 'blocked'])('needs a known unfinished peer: %s', state => {
    const g = graph(state)
    expect([...waitingEdgeIds(g)]).toEqual(['B1>C'])
    expect([...progressedEdgeIds(g)]).toEqual([])
    expect(toVueFlowEdges(g)[0]!.data).toMatchObject({ waiting: true, progressed: false, deletable: true })
  })
  it.each([null, 'done', 'unknown'])('does not invent waiting from %s', state => {
    expect([...waitingEdgeIds(graph(state))]).toEqual([])
  })
  it('ignores reference, missing, duplicate, self and requirement inputs', () => {
    const g = graph()
    for (const edges of [g.edges.slice(0, 1), [g.edges[0]!, { ...g.edges[1]!, edgeClass: 'reference' }],
      [g.edges[0]!, { ...g.edges[1]!, source: 'missing' }],
      [g.edges[0]!, { ...g.edges[0]!, id: 'duplicate' }],
      [g.edges[0]!, { ...g.edges[1]!, source: 'C' }]]) {
      expect([...waitingEdgeIds({ ...g, edges })]).toEqual([])
    }
    expect([...waitingEdgeIds({ ...g, nodes: g.nodes.map(n => n.id === 'A2' ? { ...n, kind: 'requirement' } : n) })]).toEqual([])
  })
  it.each(['in_progress', 'review', 'done', 'blocked'])('target reached: %s preserves progress protection', target => {
    const g = graph('done', target)
    expect([...waitingEdgeIds(g)]).toEqual([])
    expect([...progressedEdgeIds(g)]).toEqual(['B1>C', 'A2>C'])
    expect(toVueFlowEdges(g)[0]!.data).toMatchObject({ progressed: true, deletable: false })
  })
  it.each([
    [{}, 'pending'], [{ waiting: true }, 'waiting'], [{ progressed: true }, 'progressed'],
    [{ running: true, waiting: true, progressed: true }, 'running'],
  ] as const)('renders each state with running priority', (state, expected) => {
    const w = mount(FlowEdgePath, { props: { id: 'e', sourceX: 0, sourceY: 0, targetX: 300, targetY: 0, ...state }, global: { plugins: [i18n] } })
    expect(w.attributes('data-progress')).toBe(expected)
    expect(w.classes()).toContain('flow-edge-' + expected)
    w.unmount()
  })
})

describe('obstacle routes', () => {
  const transforms = [
    { name: 'right', f: (r: NodeRect) => r },
    { name: 'left', f: (r: NodeRect) => ({ ...r, x: 1100 - r.x - r.width }) },
    { name: 'down', f: (r: NodeRect) => ({ x: r.y, y: r.x, width: r.height, height: r.width }) },
    { name: 'up', f: (r: NodeRect) => ({ x: r.y, y: 1100 - r.x - r.width, width: r.height, height: r.width }) },
  ]
  for (const { name, f } of transforms) {
    it.each([0, 1, 3])(`${name}: avoids %i cards and retains endpoints`, count => {
      const source = f(rect(0, 0)), target = f(rect(1000, 0))
      const others = [rect(300, 0), rect(550, -80), rect(750, 60)].slice(0, count).map(f)
      const route = routeEdge(source, target, others, 16)!
      expect(route).not.toBeNull()
      expect(route.points[0]).toEqual({ x: route.anchors.sourceX, y: route.anchors.sourceY })
      expect(route.points.at(-1)).toEqual({ x: route.anchors.targetX, y: route.anchors.targetY })
      for (let i = 1; i < route.points.length; i++) {
        const a = route.points[i - 1]!, b = route.points[i]!
        expect(a.x === b.x || a.y === b.y).toBe(true)
        for (const r of others) {
          expect(segmentCrossesRect(a, b, { x: r.x - 15, y: r.y - 15, width: r.width + 30, height: r.height + 30 })).toBe(false)
        }
      }
      const drawing = routeDrawing(route)
      expect(drawing.path).not.toMatch(/NaN|undefined/)
      expect(route.points.slice(1).some((p, i) => {
        const a = route.points[i]!, m = drawing.middle
        return (a.x === p.x && m.x === a.x && m.y >= Math.min(a.y, p.y) && m.y <= Math.max(a.y, p.y))
          || (a.y === p.y && m.y === a.y && m.x >= Math.min(a.x, p.x) && m.x <= Math.max(a.x, p.x))
      })).toBe(true)
    })
  }
  it('chooses another port when the facing port is obstructed', () => {
    const obstacle = rect(190, 0)
    const route = routeEdge(rect(0, 0), rect(1000, 0), [obstacle])!
    expect(route).not.toBeNull()
    expect(route.anchors.sourcePosition).not.toBe('right')
    for (let i = 1; i < route.points.length; i++) {
      expect(segmentCrossesRect(route.points[i - 1]!, route.points[i]!, obstacle)).toBe(false)
    }
  })
  it('reports completely covered ports instead of a false safe route', () => {
    expect(routeEdge(rect(0, 0), rect(1000, 0), [{ x: -30, y: -30, width: 240, height: 180 }])).toBeNull()
  })
  it('uses fallback sizes before measurement and does not mutate inputs', () => {
    const source = nodeRect(node('B1', 0, 0)), target = nodeRect(node('C', 1000, 0))
    const obstacle = Object.freeze(nodeRect(node('A2', 400, 0)))
    const before = JSON.stringify([source, target, obstacle])
    expect(routeEdge(source, target, [obstacle])).not.toBeNull()
    expect(JSON.stringify([source, target, obstacle])).toBe(before)
    expect(source).toEqual(rect(0, 0))
  })
  it('updates hit path, arrow and remove location after obstacle movement and measurement', async () => {
    const source = node('B1', 0, 0), target = node('C', 1000, 0), obstacle = node('A2', 400, 0)
    const w = mount(FlowEdgePath, { props: { id: 'e', sourceX: 0, sourceY: 0, targetX: 0, targetY: 0,
      sourceNode: source, targetNode: target, obstacleNodes: [source, target, obstacle], selected: true, deletable: true, waiting: true },
      global: { plugins: [i18n] } })
    const path = () => w.get('.flow-edge-path').attributes('d')
    const first = path()
    expect(w.get('.flow-edge-hit').attributes('d')).toBe(first)
    expect(w.get('.flow-edge-arrow').attributes('d')).toMatch(/^M1000,54 /)
    await w.setProps({ obstacleNodes: [source, target, node('A2', 400, 300)] })
    expect(path()).not.toBe(first)
    expect(w.get('.flow-edge-hit').attributes('d')).toBe(path())
    const route = routeEdge(nodeRect(source), nodeRect(target), [rect(400, 300)])!
    const middle = routeDrawing(route).middle
    expect(w.get('.flow-edge-remove').attributes('transform')).toBe(`translate(${middle.x},${middle.y})`)
    await w.setProps({ targetNode: { ...target, dimensions: { width: 200, height: 160 } } })
    expect(w.get('.flow-edge-arrow').attributes('d')).toMatch(/^M1000,80 /)
    w.unmount()
  })
})
