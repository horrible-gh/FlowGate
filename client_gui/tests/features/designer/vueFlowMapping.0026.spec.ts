/**
 * flowgate.v02.0026 — FlowGate Graph Model -> Vue Flow mapping (0003-D §3.2 / §7.1).
 */
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { FlowGraph } from '@/features/designer/center/graph/flowGraph'
import {
  FLOWGATE_EDGE_TYPE,
  FLOWGATE_NODE_TYPE,
  toVueFlowEdges,
  toVueFlowNodes,
} from '@/features/designer/center/canvas/vueFlowMapping'

function deepFreeze<T>(value: T): T {
  if (value && typeof value === 'object') {
    Object.values(value).forEach(deepFreeze)
    Object.freeze(value)
  }
  return value
}

const GRAPH: FlowGraph = deepFreeze({
  flowId: 'f',
  nodes: [
    { id: 'f:a', kind: 'requirement', title: '요구사항 A', state: 'done', x: 0, y: 10 },
    { id: 'f:b', kind: 'task', title: '작업 B', x: 300, y: -40 },
    { id: 'f:c', kind: 'brand-new-kind', title: '새 종류', state: 'weird', x: 300, y: 200 },
  ],
  edges: [
    { id: 'f:e1', source: 'f:a', target: 'f:b' },
    { id: 'f:e2', source: 'f:a', target: 'f:c' },
    { id: 'f:dangling', source: 'f:a', target: 'f:missing' },
    { id: 'f:self', source: 'f:b', target: 'f:b' },
  ],
})

afterEach(() => vi.restoreAllMocks())

describe('toVueFlowNodes', () => {
  it('keeps Node count, ids and positions and carries kind / title / state as data', () => {
    const nodes = toVueFlowNodes(GRAPH, null)
    expect(nodes.map((n) => n.id)).toEqual(['f:a', 'f:b', 'f:c'])
    expect(nodes.map((n) => n.position)).toEqual([
      { x: 0, y: 10 },
      { x: 300, y: -40 },
      { x: 300, y: 200 },
    ])
    expect(nodes[0].data).toEqual({ kind: 'requirement', title: '요구사항 A', state: 'done', selected: false })
    expect(nodes[1].data!.state).toBeNull()
    // Unknown kind / state keys pass through untouched; the card decides how to show them.
    expect(nodes[2].data).toMatchObject({ kind: 'brand-new-kind', state: 'weird' })
  })

  it('marks only the selected Node as selected', () => {
    expect(toVueFlowNodes(GRAPH, 'f:b').map((n) => n.data!.selected)).toEqual([false, true, false])
    expect(toVueFlowNodes(GRAPH, null).some((n) => n.data!.selected)).toBe(false)
    expect(toVueFlowNodes(GRAPH, 'f:gone').some((n) => n.data!.selected)).toBe(false)
  })

  it('makes every Node read-only for the library and uses the FlowGate custom type', () => {
    for (const node of toVueFlowNodes(GRAPH, 'f:a')) {
      expect(node.type).toBe(FLOWGATE_NODE_TYPE)
      expect(node).toMatchObject({ draggable: false, connectable: false, selectable: false, focusable: false, deletable: false })
    }
  })
})

describe('toVueFlowEdges', () => {
  it('keeps id / source / target, uses the FlowGate custom type and is read-only', () => {
    vi.spyOn(console, 'warn').mockImplementation(() => {})
    const edges = toVueFlowEdges(GRAPH)
    expect(edges.map((e) => [e.id, e.source, e.target])).toEqual([
      ['f:e1', 'f:a', 'f:b'],
      ['f:e2', 'f:a', 'f:c'],
    ])
    for (const edge of edges) {
      expect(edge.type).toBe(FLOWGATE_EDGE_TYPE)
      expect(edge).toMatchObject({ updatable: false, selectable: false, focusable: false, deletable: false })
    }
  })

  it('skips dangling and self edges with a development warning', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    const ids = toVueFlowEdges(GRAPH).map((e) => e.id)
    expect(ids).not.toContain('f:dangling')
    expect(ids).not.toContain('f:self')
    expect(warn).toHaveBeenCalledTimes(2)
  })
})

describe('mapping is one way', () => {
  it('never modifies the input Graph (frozen input, unchanged output source)', () => {
    vi.spyOn(console, 'warn').mockImplementation(() => {})
    const before = JSON.stringify(GRAPH)
    const nodes = toVueFlowNodes(GRAPH, 'f:a')
    toVueFlowEdges(GRAPH)
    nodes[0].position.x = 999
    expect(JSON.stringify(GRAPH)).toBe(before)
  })
})
