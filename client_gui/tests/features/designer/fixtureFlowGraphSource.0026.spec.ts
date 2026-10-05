/**
 * flowgate.v02.0026 — sample Graph source (0003-D §3.2.3 / §7.1).
 */
import { describe, expect, it } from 'vitest'
import type { FlowGraph } from '@/features/designer/center/graph/flowGraph'
import { buildFixtureFlowGraph, fixtureFlowGraphSource } from '@/features/designer/center/graph/fixtureFlowGraphSource'

const FLOW_IDS = ['p.ui.0001', 'p.ui.0002', 'p.ui.0003', 'p.ui.0004', 'flowgate.v02.0026', 'q.m.0001', 'a', 'bb']

/** Shape = the Graph with the Flow id prefix removed. */
function shape(graph: FlowGraph): string {
  const strip = (id: string) => id.slice(graph.flowId.length)
  return JSON.stringify({
    nodes: graph.nodes.map((n) => [strip(n.id), n.kind, n.x, n.y]),
    edges: graph.edges.map((e) => [strip(e.source), strip(e.target)]),
  })
}

describe('fixtureFlowGraphSource', () => {
  it('is a sample source and resolves a FlowGate Graph of the requested Flow', async () => {
    expect(fixtureFlowGraphSource.isSample).toBe(true)
    const result = fixtureFlowGraphSource.loadGraph('p', 'p.ui.0002')
    expect(result).toBeInstanceOf(Promise)
    const graph = await result
    expect(graph.flowId).toBe('p.ui.0002')
    expect(graph.nodes.length).toBeGreaterThan(0)
  })

  it('always returns the same Graph for the same Flow id', async () => {
    for (const id of FLOW_IDS) {
      expect(await fixtureFlowGraphSource.loadGraph('p', id)).toEqual(await fixtureFlowGraphSource.loadGraph('q', id))
      expect(buildFixtureFlowGraph(id)).toEqual(buildFixtureFlowGraph(id))
    }
  })

  it('returns at least two differently shaped Graphs across Flow ids', () => {
    const shapes = new Set(FLOW_IDS.map((id) => shape(buildFixtureFlowGraph(id))))
    expect(shapes.size).toBeGreaterThanOrEqual(2)
  })

  it.each(FLOW_IDS)('%s: Node / Edge ids are unique and prefixed with the Flow id', (flowId) => {
    const graph = buildFixtureFlowGraph(flowId)
    const nodeIds = graph.nodes.map((n) => n.id)
    const edgeIds = graph.edges.map((e) => e.id)
    expect(new Set(nodeIds).size).toBe(nodeIds.length)
    expect(new Set(edgeIds).size).toBe(edgeIds.length)
    for (const id of [...nodeIds, ...edgeIds]) expect(id.startsWith(`${flowId}:`)).toBe(true)
  })

  it.each(FLOW_IDS)('%s: every Edge joins two existing Nodes and none is a self edge', (flowId) => {
    const graph = buildFixtureFlowGraph(flowId)
    const nodeIds = new Set(graph.nodes.map((n) => n.id))
    for (const edge of graph.edges) {
      expect(nodeIds.has(edge.source)).toBe(true)
      expect(nodeIds.has(edge.target)).toBe(true)
      expect(edge.source).not.toBe(edge.target)
    }
  })

  it.each(FLOW_IDS)('%s: free placement with a branch and a join, not one row', (flowId) => {
    const graph = buildFixtureFlowGraph(flowId)
    expect(new Set(graph.nodes.map((n) => n.y)).size).toBeGreaterThan(1)
    const outDegree = new Map<string, number>()
    const inDegree = new Map<string, number>()
    for (const edge of graph.edges) {
      outDegree.set(edge.source, (outDegree.get(edge.source) ?? 0) + 1)
      inDegree.set(edge.target, (inDegree.get(edge.target) ?? 0) + 1)
    }
    expect(Math.max(...outDegree.values())).toBeGreaterThan(1)
    expect(Math.max(...inDegree.values())).toBeGreaterThan(1)
  })

  it('sample titles follow the Korean wording rule (no Flow / 흐름 / 플로 without 우)', () => {
    for (const id of FLOW_IDS) {
      for (const node of buildFixtureFlowGraph(id).nodes) expect(node.title).not.toMatch(/Flow|흐름|플로(?!우)/)
    }
  })
})
