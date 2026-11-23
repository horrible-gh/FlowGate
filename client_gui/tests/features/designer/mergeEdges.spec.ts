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

describe('obstacle-aware orthogonal routes', () => {
  it.each([[600, 100], [-600, -100], [100, 600], [-100, -600]])(
    'uses the same route standalone and on the canvas for %s,%s', (x, y) => {
      const source = rect(0, 0), target = rect(x, y)
      expect(routeEdges([{ id: 'e', source: 's', target: 't' }], new Map([['s', source], ['t', target]])).get('e')!.points)
        .toEqual(routeEdge(source, target).points)
    })
  it('avoids an intervening card and keeps the label on the route', () => {
    const obstacle = rect(400, 0)
    const route = routeEdge(rect(0, 0), rect(1000, 100), [obstacle])
    expect(route.points.some((p, i) => i > 0 && segmentCrossesRect(route.points[i - 1]!, p, obstacle))).toBe(false)
    const { path, middle } = routeDrawing(route)
    expect(path).toMatch(/^M/)
    expect(route.points.some((p, i) => i > 0 && (
      p.x === middle.x && route.points[i - 1]!.x === middle.x && middle.y >= Math.min(p.y, route.points[i - 1]!.y) && middle.y <= Math.max(p.y, route.points[i - 1]!.y)
      || p.y === middle.y && route.points[i - 1]!.y === middle.y && middle.x >= Math.min(p.x, route.points[i - 1]!.x) && middle.x <= Math.max(p.x, route.points[i - 1]!.x)
    ))).toBe(true)
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
  it('places 5 after 3/4 and at the mean of all three inputs; moving 4 onto 2→5 diverts it', () => {
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
    expect(moved.points).not.toEqual(edge.points)
    expect(moved.points.some((p,i) => i > 0 && segmentCrossesRect(moved.points[i-1]!,p,nodes.get('4')!))).toBe(false)
    expect([...routes.values()].filter(r => r.anchors.targetX === nodes.get('5')!.x && r.drawArrow)).toHaveLength(1)
    const after = { ...g, nodes:g.nodes.map(n => ({...n,...at.get(n.id)})) }
    expect(planDirectionalLayout(after,undefined,'horizontal').candidate).toEqual(plan.candidate)
  })
})
