// flowgate.v02.0026 — FlowGate Graph Model -> Vue Flow input, one way (0003-D §3.2). The only
// pure file that knows Vue Flow's node / edge shape. Vue Flow objects are rebuilt from the
// Graph whenever it is drawn and never written back; the Graph itself is never modified.
//
// Every element is read-only for the library (no select / focus / delete): FlowGate owns the
// selection (Center session) and passes it in as `data.selected`. flowgate.v02.0045: a Node the
// Graph marks `movable` can be dragged; the drop is reported back to the session as a move, Vue
// Flow's own position is never the source of truth. flowgate.v02.0045 S3: a node of an editable
// draft (`structureEditable`) can start / end a connection; the new edge is reported to the
// session as a connect command, never added by Vue Flow itself.
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
  /** flowgate.v02.0045 S3: the node takes connections (a node of an editable draft). */
  connectable?: boolean
  /** flowgate.v02.0045 S3: the last validation reported a problem on this node. */
  issue?: boolean
}

export type FlowGateVueFlowNode = Node<FlowGateNodeData>

/** flowgate.v02.0045: the meaning class of an Edge, drawn as a solid or a dashed line. */
export interface FlowGateEdgeData {
  edgeClass: string | null
  /** flowgate.v02.0045 S3: an execution edge of an editable draft (it can be removed). */
  editable?: boolean
  issue?: boolean
}

export function toVueFlowNodes(
  graph: FlowGraph,
  selectedNodeId: string | null,
  issueIds: ReadonlySet<string> = new Set(),
): FlowGateVueFlowNode[] {
  return graph.nodes.map((node) => {
    const connectable = node.structureEditable === true && !!node.definition
    const data: FlowGateNodeData = { kind: node.kind, title: node.title, state: node.state ?? null, selected: node.id === selectedNodeId }
    if (connectable) data.connectable = true
    if (issueIds.has(node.id)) data.issue = true
    return {
      id: node.id,
      type: FLOWGATE_NODE_TYPE,
      position: { x: node.x, y: node.y },
      data,
      draggable: node.movable === true,
      connectable,
      selectable: false,
      focusable: false,
      deletable: false,
    }
  })
}

/**
 * Edges whose source or target Node is not in the Graph, and self edges, are skipped (with a
 * development warning only).
 */
export function toVueFlowEdges(graph: FlowGraph, issueIds: ReadonlySet<string> = new Set()): Edge[] {
  const nodeIds = new Set(graph.nodes.map((node) => node.id))
  const edges: Edge[] = []
  for (const edge of graph.edges) {
    if (edge.source === edge.target || !nodeIds.has(edge.source) || !nodeIds.has(edge.target)) {
      if (import.meta.env.DEV) console.warn(`[designer canvas] edge ${edge.id} skipped: dangling or self edge`)
      continue
    }
    const data: FlowGateEdgeData = { edgeClass: edge.edgeClass ?? null }
    if (edge.editable === true) data.editable = true
    if (issueIds.has(edge.id)) data.issue = true
    edges.push({
      id: edge.id,
      type: FLOWGATE_EDGE_TYPE,
      source: edge.source,
      target: edge.target,
      data,
      updatable: false,
      selectable: false,
      focusable: false,
      deletable: false,
    })
  }
  return edges
}
