// flowgate.v02.0026 — FlowGate Graph Model -> Vue Flow input, one way (0003-D §3.2). The only
// pure file that knows Vue Flow's node / edge shape. Vue Flow objects are rebuilt from the
// Graph whenever it is drawn and never written back; the Graph itself is never modified.
//
// Every element is read-only for the library (no drag / connect / select / focus / delete):
// FlowGate owns the selection (Center session) and passes it in as `data.selected`.
import type { Edge, Node } from '@vue-flow/core'
import type { FlowGraph } from '../graph/flowGraph'

/** Custom node / edge type key: FlowCanvas renders it with FlowNodeCard / FlowEdgePath. */
export const FLOWGATE_NODE_TYPE = 'flowgate'
export const FLOWGATE_EDGE_TYPE = 'flowgate'

export interface FlowGateNodeData {
  kind: string
  title: string
  state: string | null
  selected: boolean
}

export type FlowGateVueFlowNode = Node<FlowGateNodeData>

export function toVueFlowNodes(graph: FlowGraph, selectedNodeId: string | null): FlowGateVueFlowNode[] {
  return graph.nodes.map((node) => ({
    id: node.id,
    type: FLOWGATE_NODE_TYPE,
    position: { x: node.x, y: node.y },
    data: { kind: node.kind, title: node.title, state: node.state ?? null, selected: node.id === selectedNodeId },
    draggable: false,
    connectable: false,
    selectable: false,
    focusable: false,
    deletable: false,
  }))
}

/**
 * Edges whose source or target Node is not in the Graph, and self edges, are skipped (with a
 * development warning only).
 */
export function toVueFlowEdges(graph: FlowGraph): Edge[] {
  const nodeIds = new Set(graph.nodes.map((node) => node.id))
  const edges: Edge[] = []
  for (const edge of graph.edges) {
    if (edge.source === edge.target || !nodeIds.has(edge.source) || !nodeIds.has(edge.target)) {
      if (import.meta.env.DEV) console.warn(`[designer canvas] edge ${edge.id} skipped: dangling or self edge`)
      continue
    }
    edges.push({
      id: edge.id,
      type: FLOWGATE_EDGE_TYPE,
      source: edge.source,
      target: edge.target,
      updatable: false,
      selectable: false,
      focusable: false,
      deletable: false,
    })
  }
  return edges
}
