import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import type { GraphNode } from '@vue-flow/core'
import { progressedEdgeIds } from '@/features/designer/center/graph/edgeProgress'
import { toVueFlowEdges } from '@/features/designer/center/canvas/vueFlowMapping'
import { routeEdge, routeEdges, routeDrawing, segmentCrossesRect } from '@/features/designer/center/canvas/edgeRouting'
import { nodeRect, type NodeRect } from '@/features/designer/center/canvas/edgeGeometry'
import { planDirectionalLayout, checkDirectionalLayout } from '@/features/designer/center/arrange/directionalLayout'
import type { FlowGraph } from '@/features/designer/center/graph/flowGraph'
import FlowEdgePath from '@/features/designer/center/canvas/FlowEdgePath.vue'

const rect = (x: number, y: number): NodeRect => ({ x, y, width: 176, height: 108 })
const node = (id: string, x: number, y: number): GraphNode =>
  ({ id, computedPosition: { x, y, z: 0 }, dimensions: { width: 0, height: 0 } }) as GraphNode
const i18n = createI18n({ legacy: false, locale: 'en', messages: { en: {} }, missingWarn: false, fallbackWarn: false })
const graph = (target: string): FlowGraph => ({
  flowId: 'test', nodes: [{ id: 'B1', state: 'done' }, { id: 'A2', state: 'in_progress' }, { id: 'C', state: target }]
    .map(n => ({ ...n, kind: 'task', title: n.id, x: 0, y: 0 })),
  edges: ['B1', 'A2'].map(source => ({ id: source + '>C', source, target: 'C', edgeClass: 'execution', editable: true })),
})

describe('target state controls incoming line colours, independent of deletion protection', () => {
  it.each(['draft', 'in_progress', 'review', 'done', 'blocked'])('%s gives all incoming edges the same appearance', target => {
    const g = graph(target)
    const edges = toVueFlowEdges(g)
    const wrappers = edges.map(e => mount(FlowEdgePath, { props: {
      id: e.id, sourceX: 0, sourceY: 0, targetX: 300, targetY: 0,
      progressed: e.data!.progressed, targetState: target,
    }, global: { plugins: [i18n] } }))
    const expected = target === 'done' ? 'progressed' : ['in_progress', 'review'].includes(target) ? 'running' : 'pending'
    expect(wrappers.map(w => w.get('.flow-edge').attributes('data-progress'))).toEqual([expected, expected])
    wrappers.forEach(w => w.unmount())
    expect(edges.every(e => !('waiting' in e.data!))).toBe(true)
    expect(edges[0]!.data?.deletable).toBe(!progressedEdgeIds(g).has('B1>C'))
  })
  it('a completed source waiting for another input uses a neutral solid line', () => {
    const data = toVueFlowEdges(graph('draft'))[0]!.data!
    const w = mount(FlowEdgePath, { props: { id: 'e', sourceX: 0, sourceY: 0, targetX: 300, targetY: 0, ...data }, global: { plugins: [i18n] } })
    expect(w.get('.flow-edge').attributes('data-progress')).toBe('pending')
    expect(w.get('.flow-edge').classes()).not.toContain('flow-edge-waiting')
    w.unmount()
  })
})

describe('direct orthogonal routes under cards', () => {
  it.each([[600, 100], [-600, -100], [100, 600], [-100, -600]])(
    'centres a single connection between ports for offset %s,%s', (x, y) => {
      const source = rect(0, 0), target = rect(x, y)
      const nodes = new Map([['s', source], ['t', target]])
      const edge = { id: 'st', source: 's', target: 't' }
      for (const route of [routeEdge(source, target), routeEdges([edge], nodes).get('st')!]) {
        const a = route.anchors
        if (a.sourcePosition === 'left' || a.sourcePosition === 'right') {
          expect(route.points.slice(1, 3).map(p => p.x)).toEqual([
            (a.sourceX + a.targetX) / 2, (a.sourceX + a.targetX) / 2,
          ])
        } else {
          expect(route.points.slice(1, 3).map(p => p.y)).toEqual([
            (a.sourceY + a.targetY) / 2, (a.sourceY + a.targetY) / 2,
          ])
        }
      }
      nodes.set('s2', rect(-100, 20))
      const merged = routeEdges([edge, { id: 's2t', source: 's2', target: 't' }], nodes)
      expect([...merged.values()].filter(r => r.drawArrow)).toHaveLength(1)
      expect(routeEdges([edge], nodes).get('st')!.points).toEqual(routeEdge(source, target).points)
    })

  it.each([[180, 150, 'bottom', 'top'], [-180, -150, 'top', 'bottom'],
    [300, 40, 'right', 'left'], [-300, -40, 'left', 'right']])(
    'chooses facing sides for offset %s,%s without crossing either endpoint card', (x, y, from, to) => {
      const source = rect(0, 0), target = rect(Number(x), Number(y))
      const route = routeEdge(source, target)
      expect(route.anchors.sourcePosition).toBe(from)
      expect(route.anchors.targetPosition).toBe(to)
      for (let i = 1; i < route.points.length; i++) {
        expect(segmentCrossesRect(route.points[i - 1]!, route.points[i]!, source)).toBe(false)
        expect(segmentCrossesRect(route.points[i - 1]!, route.points[i]!, target)).toBe(false)
      }
    })
  it.each(['right', 'left', 'down', 'up'])('centres merged inputs heading %s in either input order', direction => {
    const nodes = new Map(direction === 'right'
      ? [['a', rect(0, 0)], ['b', rect(0, 200)], ['t', rect(600, 100)]]
      : direction === 'left'
        ? [['a', rect(600, 0)], ['b', rect(600, 200)], ['t', rect(0, 100)]]
        : direction === 'down'
          ? [['a', rect(0, 0)], ['b', rect(200, 0)], ['t', rect(100, 600)]]
          : [['a', rect(0, 600)], ['b', rect(200, 600)], ['t', rect(100, 0)]])
    const edges = ['a', 'b'].map(source => ({ id: source, source, target: 't' }))
    const horizontal = direction === 'right' || direction === 'left'
    for (const ordered of [edges, [...edges].reverse()]) {
      const routes = [...routeEdges(ordered, nodes).values()]
      expect(routes.filter(r => r.drawArrow)).toHaveLength(1)
      for (const route of routes) {
        const a = route.anchors
        const midpoint = horizontal ? (a.sourceX + a.targetX) / 2 : (a.sourceY + a.targetY) / 2
        expect(route.points.slice(1, 3).map(p => horizontal ? p.x : p.y)).toEqual([midpoint, midpoint])
      }
      expect(routes[0]!.points.slice(-2)).toEqual(routes[1]!.points.slice(-2))
    }
  })
  it('centres a wide shared gap against the nearest input with different source columns', () => {
    const nodes = new Map([['far', rect(0, 0)], ['near', rect(200, 100)], ['t', rect(800, 50)]])
    const edges = ['far', 'near'].map(source => ({ id: source, source, target: 't' }))
    for (const ordered of [edges, [...edges].reverse()]) {
      expect([...routeEdges(ordered, nodes).values()].map(r => r.points[1]!.x)).toEqual([588, 588])
    }
  })
  it('keeps a shared trunk inside a narrow gap regardless of input order', () => {
    const nodes = new Map([['far', rect(0, 0)], ['near', rect(400, 40)], ['t', rect(600, 60)]])
    const edges = ['far', 'near'].map(source => ({ id: source, source, target: 't' }))
    for (const ordered of [edges, [...edges].reverse()]) {
      const routes = routeEdges(ordered, nodes)
      expect(new Set([...routes.values()].map(r => r.points[1]!.x))).toEqual(new Set([588]))
      expect([...routes.values()].filter(r => r.drawArrow)).toHaveLength(1)
      for (const edge of ordered) {
        const route = routes.get(edge.id)!
        for (let i = 1; i < route.points.length; i++) {
          expect(segmentCrossesRect(route.points[i - 1]!, route.points[i]!, nodes.get(edge.source)!)).toBe(false)
          expect(segmentCrossesRect(route.points[i - 1]!, route.points[i]!, nodes.get('t')!)).toBe(false)
        }
      }
    }
  })
  it('passes through a card instead of detouring; keeps endpoints and the label on the route', () => {
    const route = routeEdge(rect(0, 0), rect(1000, 100))
    const obstacle = rect(400, 0)
    expect(route.points.some((p, i) => i > 0 && segmentCrossesRect(route.points[i - 1]!, p, obstacle))).toBe(true)
    expect(route.points).toHaveLength(4)
    expect(route.points[0]).toEqual({ x: 176, y: 54 })
    expect(route.points.at(-1)).toEqual({ x: 1000, y: 154 })
    expect(routeDrawing(route)).toEqual({ path: 'M176,54 L588,54 L588,154 L991,154', middle: { x: 588, y: 99.5 } })
  })
  it.each([3, 8, 20])('%i inputs share one side-centre port and one arrow', count => {
    const nodes = new Map([['T', rect(5000, 200)]])
    const edges = Array.from({ length: count }, (_, i) => {
      nodes.set('s' + i, rect(0, i * 156))
      return { id: 'e' + i, source: 's' + i, target: 'T' }
    })
    const routes = [...routeEdges(edges, nodes).values()]
    expect(routes.filter(r => r.drawArrow)).toHaveLength(1)
    expect(new Set(routes.map(r => JSON.stringify(r.points.slice(-2)))).size).toBe(1)
  })
  it('centres merged targets on the same column without shifting their trunks', () => {
    const nodes = new Map([['a', rect(0, 0)], ['b', rect(0, 200)], ['c', rect(600, 200)], ['d', rect(600, 0)]])
    const routes = routeEdges([{ id: 'ac', source: 'a', target: 'c' }, { id: 'bc', source: 'b', target: 'c' },
      { id: 'bd', source: 'b', target: 'd' }, { id: 'ad', source: 'a', target: 'd' }], nodes)
    expect([...routes.values()].map(r => r.points[1]!.x)).toEqual([388, 388, 388, 388])
    expect([...routes.values()].filter(r => r.drawArrow)).toHaveLength(2)
  })
  it('follows endpoint movement and size; hides duplicate arrows while keeping selection and removal', async () => {
    const source = node('s', 0, 0), target = node('t', 1000, 100)
    const w = mount(FlowEdgePath, { props: { id: 'e', sourceX: 0, sourceY: 0, targetX: 0, targetY: 0,
      sourceNode: source, targetNode: target, selected: true, deletable: true }, global: { plugins: [i18n] } })
    const first = w.get('.flow-edge-path').attributes('d')
    await w.setProps({ targetNode: node('t', 800, 200) })
    expect(w.get('.flow-edge-path').attributes('d')).not.toBe(first)
    expect(w.get('.flow-edge-hit').attributes('d')).toBe(w.get('.flow-edge-path').attributes('d'))
    await w.setProps({ routedEdge: { ...routeEdge(nodeRect(source), nodeRect(target)), drawArrow: false } })
    expect(w.find('.flow-edge-arrow').exists()).toBe(false)
    await w.get('.flow-edge-remove').trigger('click')
    expect(w.emitted('remove')).toHaveLength(1)
    w.unmount()
  })
})

describe('0069 branch and three-input join layout', () => {
  const example = (): FlowGraph => ({ flowId: 'f', projection: 'graph',
    nodes: ['R', '1', '2', '3', '4', '5'].map(id => ({ id, kind: id === 'R' ? 'requirement' : 'task', title: id,
      x: 900, y: 900, movable: true, definition: { nodeType: 'document', docType: id === 'R' ? 'R' : 'T', label: id, paletteItemId: null, assignee: null } })),
    edges: [['R','1'],['R','2'],['1','3'],['1','4'],['2','5'],['3','5'],['4','5']]
      .map(([source,target], i) => ({ id: 'e'+i, source: source!, target: target!, edgeClass: 'execution' })),
  })
  it('places 5 after 3/4 and at the mean of all three inputs; moving 4 onto 2→5 does not divert it', () => {
    const g = example()
    const plan = planDirectionalLayout(g, undefined, 'horizontal')
    expect(checkDirectionalLayout(plan)).toBeNull()
    const at = new Map(plan.candidate.map(p => [p.nodeId,p]))
    expect(at.get('5')!.x).toBeGreaterThan(at.get('4')!.x)
    expect(at.get('5')!.y).toBeCloseTo((at.get('2')!.y + at.get('3')!.y + at.get('4')!.y) / 3, 0)
    const nodes = new Map([...at].map(([id,p]) => [id,rect(p.x,p.y)]))
    const routes = routeEdges(g.edges,nodes)
    const edge = routes.get('e4')!
    nodes.set('4', { ...nodes.get('4')!, y: nodes.get('2')!.y })
    const moved = routeEdges(g.edges,nodes).get('e4')!
    expect(moved.points).toEqual(edge.points)
    expect(moved.points.some((p,i) => i > 0 && segmentCrossesRect(moved.points[i-1]!,p,nodes.get('4')!))).toBe(true)
    expect([...routes.values()].filter(r => r.anchors.targetX === nodes.get('5')!.x && r.drawArrow)).toHaveLength(1)
    const after = { ...g, nodes:g.nodes.map(n => ({...n,...at.get(n.id)})) }
    expect(planDirectionalLayout(after,undefined,'horizontal').candidate).toEqual(plan.candidate)
  })
})
