// flowgate.v02.0045 S3 — the Graph draft the Center edits (D0004 §2.3 center/edit "편집 명령"):
// the draft's nodes and execution edges taken from the shown Graph, the whole-draft content a
// save sends, key remapping once a save answers the keys of new nodes / edges, and the shown
// Graph rebuilt from an edited draft. Pure: no Vue, no transport.
// flowgate.v02.0045 TR0020 rev3: a Graph Flow's latest revision shown with no draft is offered for
// editing as is (`editableRevision`) — its first edit saves a new draft (version 0, the revision's
// nodes and edges with their keys), so 편집 needs no button and the revision itself never changes.
import type { FlowGraph, FlowGraphEdge, FlowGraphNode, FlowGraphNodeDefinition } from '../graph/flowGraph'
import type { FlowDraftContent } from '../graph/flowGraphSource'

export const CLIENT_KEY_PREFIX = 'tmp_'

export interface DraftNode {
  /** Stored node key, or a `tmp_` client key while `isNew`. */
  readonly key: string
  readonly isNew: boolean
  readonly nodeType: string
  readonly docType: string | null
  readonly paletteItemId: string | null
  readonly label: string
  readonly assignee: FlowGraphNodeDefinition['assignee']
  /** Display only: the Canvas kind key the node is drawn with. */
  readonly kind: string
  readonly x: number
  readonly y: number
}

export interface DraftEdge {
  readonly key: string
  readonly isNew: boolean
  readonly source: string
  readonly target: string
}

export interface DraftModel {
  readonly nodes: readonly DraftNode[]
  readonly edges: readonly DraftEdge[]
}

/** What a Palette drop asks the Canvas to create (handed over by the Shell). */
export interface CanvasNodeSpec {
  /** `document` | `gate` | `custom` (P0008 §5.1). */
  readonly nodeType: string
  readonly docType: string | null
  readonly paletteItemId: string | null
  readonly label: string
  readonly kind: string
}

export const EMPTY_DRAFT: DraftModel = Object.freeze({ nodes: [], edges: [] })

const isDraftNode = (node: FlowGraphNode) => node.structureEditable === true && !!node.definition

/** The editable draft shown in `graph` (its draft nodes and the editable edges between them). */
export function draftFromGraph(graph: FlowGraph): DraftModel {
  const nodes: DraftNode[] = graph.nodes.filter(isDraftNode).map((node) => ({
    key: node.id,
    isNew: node.id.startsWith(CLIENT_KEY_PREFIX),
    nodeType: node.definition!.nodeType,
    docType: node.definition!.docType,
    paletteItemId: node.definition!.paletteItemId,
    label: node.definition!.label,
    assignee: node.definition!.assignee,
    kind: node.kind,
    x: node.x,
    y: node.y,
  }))
  const keys = new Set(nodes.map((n) => n.key))
  const edges = graph.edges
    .filter((edge) => edge.editable === true && keys.has(edge.source) && keys.has(edge.target))
    .map((edge) => ({ key: edge.id, isNew: edge.id.startsWith(CLIENT_KEY_PREFIX), source: edge.source, target: edge.target }))
  return { nodes, edges }
}

export function draftContent(model: DraftModel): FlowDraftContent {
  return {
    nodes: model.nodes.map(({ key, isNew, nodeType, docType, paletteItemId, label, assignee }) => ({
      key, isNew, nodeType, docType, paletteItemId, label, assignee,
    })),
    edges: model.edges.map(({ key, isNew, source, target }) => ({ key, isNew, source, target })),
  }
}

const mapKey = (key: string, keyMap: Readonly<Record<string, string>>) => keyMap[key] ?? key

export function remapDraft(model: DraftModel, keyMap: Readonly<Record<string, string>>): DraftModel {
  return {
    nodes: model.nodes.map((n) => (keyMap[n.key] ? { ...n, key: keyMap[n.key], isNew: false } : n)),
    edges: model.edges.map((e) => ({
      ...e,
      key: mapKey(e.key, keyMap),
      isNew: keyMap[e.key] ? false : e.isNew,
      source: mapKey(e.source, keyMap),
      target: mapKey(e.target, keyMap),
    })),
  }
}

/**
 * TR0020 rev3: a Graph Flow showing its latest revision with no draft, made editable — its Graph
 * nodes and execution edges become the draft part, saved as a new draft (base = that revision) by
 * the first edit. Else null: another ownership or view, a stored draft (it is shown instead), or a
 * run pinned to an older revision (editing it would copy that older structure).
 */
export function editableRevision(graph: FlowGraph): FlowGraph | null {
  const latest = graph.latestRevisionNo ?? null
  if (graph.ownership !== 'graph' || graph.viewMode !== 'revision' || graph.draft || latest === null) return null
  if ((graph.viewRevisionNo ?? latest) !== latest) return null
  return {
    ...graph,
    nodes: graph.nodes.map((node) => (node.definition ? { ...node, structureEditable: true } : node)),
    edges: graph.edges.map((edge) => (edge.edgeClass === 'execution' ? { ...edge, editable: true } : edge)),
    draft: { version: 0, editable: true, confirmable: true, blockedReason: null, baseRevisionNo: latest },
  }
}

/** `graph` with its draft part replaced by `model` (other nodes / edges and positions kept). */
export function withDraft(graph: FlowGraph, model: DraftModel): FlowGraph {
  const shown = new Map(graph.nodes.map((node) => [node.id, node]))
  const kept = graph.nodes.filter((node) => !isDraftNode(node))
  const nodes: FlowGraphNode[] = model.nodes.map((n) => {
    const before = shown.get(n.key)
    return {
      id: n.key,
      kind: before?.kind ?? n.kind,
      title: before?.title ?? n.label,
      state: before?.state ?? 'draft',
      docType: before?.docType ?? n.docType,
      x: before?.x ?? n.x,
      y: before?.y ?? n.y,
      positionSource: before?.positionSource ?? 'saved',
      movable: true,
      structureEditable: true,
      definition: {
        nodeType: n.nodeType, docType: n.docType, paletteItemId: n.paletteItemId, label: n.label,
        assignee: n.assignee, rootBound: before?.definition?.rootBound ?? false,
      },
    }
  })
  // Draft edges are exactly the editable ones; reference edges to a removed node are skipped
  // when drawn (vueFlowMapping drops dangling edges).
  const keptEdges = graph.edges.filter((edge) => edge.editable !== true)
  const edges: FlowGraphEdge[] = model.edges.map((e) => ({
    id: e.key, source: e.source, target: e.target, edgeClass: 'execution', editable: true,
  }))
  return { ...graph, nodes: [...kept, ...nodes], edges: [...keptEdges, ...edges] }
}

/** `graph` with node / edge ids renamed after a save (selection and positions follow the node). */
export function remapGraph(graph: FlowGraph, keyMap: Readonly<Record<string, string>>): FlowGraph {
  return {
    ...graph,
    nodes: graph.nodes.map((node) => (keyMap[node.id] ? { ...node, id: keyMap[node.id] } : node)),
    edges: graph.edges.map((edge) => ({
      ...edge, id: mapKey(edge.id, keyMap), source: mapKey(edge.source, keyMap), target: mapKey(edge.target, keyMap),
    })),
  }
}
