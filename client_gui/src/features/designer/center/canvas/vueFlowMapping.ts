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
//
// flowgate.v02.0045 T0019: an Edge carries whether the work already went through it
// (graph/edgeProgress.ts), whether it is the Canvas's selected Edge and — apart from that —
// whether it may be removed: only an editable draft Edge that is not progressed.
//
// flowgate.v02.0045 TR0020 rev4 (rejection item 2): only execution Edges are drawn — one kind of
// line, in the direction the work goes. A reference relation (`edgeClass` 'reference': the
// triggered_by / target document relations the server also sends) is left out, so a 요구사항
// card no longer fans out dashed lines to every document it triggered. `issues`: the nodes / edges
// the last validation reported, with the text shown on hover.
//
// flowgate.v02.0059 T#1 (0011-CH contract A / B): the Selection may hold several Nodes. In the
// Toolbar's [선택] mode (`selectMode`) Nodes are selectable for the library — its area selection and
// click toggle report what changed, FlowGate keeps the Selection — and carry Vue Flow's `selected`,
// so dragging a selected Node moves every selected movable Node together. Only a selected Node can be
// dragged then (a click selects it first); a Node the Graph does not mark `movable` never moves. Out
// of the mode Nodes are mapped as before and are never `selected` for the library (it keeps a Node's
// earlier fields on a redraw, so a selection left from the mode is cleared explicitly).
import type { Edge, Node } from '@vue-flow/core'
import { progressedEdgeIds } from '../graph/edgeProgress'
import type { FlowGraph } from '../graph/flowGraph'

/** Custom node / edge type key: FlowCanvas renders it with FlowNodeCard / FlowEdgePath. */
export const FLOWGATE_NODE_TYPE = 'flowgate'
export const FLOWGATE_EDGE_TYPE = 'flowgate'

export interface FlowGateNodeData {
  kind: string
  /** flowgate.v02.0045 T0019: the document type, so a 설계 card is named like its Palette card. */
  docType?: string | null
  title: string
  state: string | null
  selected: boolean
  /** flowgate.v02.0045 S3: the node takes connections (a node of an editable draft). */
  connectable?: boolean
  /** flowgate.v02.0045 S3: the last validation reported a problem on this node. */
  issue?: boolean
  /** What it reported (hover text). */
  issueText?: string
}

/** `selected` is Vue Flow's own flag (its drag moves every selected Node); FlowGate always sets it. */
export type FlowGateVueFlowNode = Node<FlowGateNodeData> & { selected?: boolean }

/** flowgate.v02.0045: the meaning class of an Edge, drawn as a solid or a dashed line. */
export interface FlowGateEdgeData {
  edgeClass: string | null
  /** flowgate.v02.0045 S3: an execution edge of an editable draft (it can be removed). */
  editable?: boolean
  issue?: boolean
  issueText?: string
  /** flowgate.v02.0045 T0019: the work already went through this Edge (its actual state). */
  progressed: boolean
  /** flowgate.v02.0045 T0019: the Canvas's selected Edge. */
  selected?: boolean
  /** flowgate.v02.0045 T0019: the Edge may be removed (editable and not progressed). */
  deletable: boolean
}

export interface VueFlowNodeOptions {
  /** flowgate.v02.0059 T#1: the Toolbar's [선택] mode is on. */
  selectMode?: boolean
}

export function toVueFlowNodes(
  graph: FlowGraph,
  selection: string | null | readonly string[],
  issues: ReadonlyMap<string, string> = new Map(),
  options: VueFlowNodeOptions = {},
): FlowGateVueFlowNode[] {
  const selectedIds = new Set<string>(selection === null ? [] : typeof selection === 'string' ? [selection] : selection)
  return graph.nodes.map((node) => {
    const connectable = node.structureEditable === true && !!node.definition
    const selected = selectedIds.has(node.id)
    const data: FlowGateNodeData = { kind: node.kind, title: node.title, state: node.state ?? null, selected }
    if (node.docType) data.docType = node.docType
    if (connectable) data.connectable = true
    if (issues.has(node.id)) {
      data.issue = true
      data.issueText = issues.get(node.id)
    }
    const mapped: FlowGateVueFlowNode = {
      id: node.id,
      type: FLOWGATE_NODE_TYPE,
      position: { x: node.x, y: node.y },
      data,
      draggable: node.movable === true,
      connectable,
      selectable: false,
      selected: false,
      focusable: false,
      deletable: false,
    }
    if (options.selectMode) {
      mapped.selectable = true
      mapped.selected = selected
      mapped.draggable = node.movable === true && selected
    }
    return mapped
  })
}

/**
 * Edges whose source or target Node is not in the Graph, and self edges, are skipped (with a
 * development warning only). Reference relations are not drawn at all (TR0020 rev4).
 */
export function toVueFlowEdges(
  graph: FlowGraph,
  issues: ReadonlyMap<string, string> = new Map(),
  selectedEdgeId: string | null = null,
): Edge[] {
  const nodeIds = new Set(graph.nodes.map((node) => node.id))
  const progressed = progressedEdgeIds(graph)
  const edges: Edge[] = []
  for (const edge of graph.edges) {
    if (edge.edgeClass === 'reference') continue
    if (edge.source === edge.target || !nodeIds.has(edge.source) || !nodeIds.has(edge.target)) {
      if (import.meta.env.DEV) console.warn(`[designer canvas] edge ${edge.id} skipped: dangling or self edge`)
      continue
    }
    const isProgressed = progressed.has(edge.id)
    const data: FlowGateEdgeData = {
      edgeClass: edge.edgeClass ?? null,
      progressed: isProgressed,
      deletable: edge.editable === true && !isProgressed,
    }
    if (edge.editable === true) data.editable = true
    if (issues.has(edge.id)) {
      data.issue = true
      data.issueText = issues.get(edge.id)
    }
    if (edge.id === selectedEdgeId) data.selected = true
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
