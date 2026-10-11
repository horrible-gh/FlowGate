// flowgate.v02.0048 T#1 — what the bottom Inspector works on (D#1 §4.3). One pure translation of
// the Center session's selection context (FlowGraphSelection) into an InspectorTarget: the
// document bound to the selected Node, its card kind, its states and whether the Flow is read
// only. Every Inspector part reads this one shape, so the rules here (card kind, read only) are
// decided once and never again per feature.
import type {
  FlowGraphCardDocument,
  FlowGraphNodeRun,
  FlowGraphNodeSlot,
  FlowGraphSelection,
} from '../center/graph/flowGraph'

/** Inspector card kinds (D#1 §4.3) — the one value set registrations may name. */
export const INSPECTOR_CARD_KINDS = ['requirement', 'research', 'task', 'design', 'test', 'check', 'plan', 'other'] as const

export type InspectorCardKind = (typeof INSPECTOR_CARD_KINDS)[number]

/** Document types of each card kind; `other` takes every remaining (open) type. */
export const INSPECTOR_CARD_DOC_TYPES: Readonly<Record<Exclude<InspectorCardKind, 'other'>, readonly string[]>> = {
  requirement: ['R', 'B'],
  research: ['N', 'NR'],
  task: ['T', 'TR', 'T2', 'TR2'],
  design: ['DS', 'D', 'P', 'L', 'DB'],
  test: ['TS', 'TSR', 'V'],
  check: ['AC'],
  plan: ['WP'],
}

/** The document types a registration's `docTypes` may name (every type outside `other`). */
export const INSPECTOR_DOC_TYPES: readonly string[] = Object.values(INSPECTOR_CARD_DOC_TYPES).flat()

/** Flow ownership keys a registration's `ownerships` may name. */
export const INSPECTOR_OWNERSHIPS = ['unassigned', 'v01', 'graph', 'conflict'] as const

/** One document line of a card (FlowGraphCardDocument). */
export type InspectorCardDocument = FlowGraphCardDocument

/** Why the Inspector may not change anything: ownership conflict, or a Graph revision view. */
export type InspectorReadOnly = 'conflict' | 'revision' | null

export interface InspectorTarget {
  readonly projectId: string
  /** Flow id = v0.1 group id. */
  readonly flowId: string
  /** Canvas Node identity (node_key). */
  readonly nodeId: string
  /** Canvas Node kind key, for the header look only (flowNodeKinds.ts). */
  readonly nodeKind: string
  /** The live document bound to the Node, or null. */
  readonly docId: string | null
  /** Document type: the bound document's, else the Graph definition's, else the Node's. */
  readonly docType: string | null
  readonly cardKind: InspectorCardKind
  readonly title: string
  /** Canvas state key (5 kinds, flowNodeKinds.ts) — the state chip. */
  readonly canvasState: string | null
  readonly docReviewStatus: string | null
  readonly docStatus: string | null
  readonly supersedes: readonly string[]
  readonly slot: FlowGraphNodeSlot | null
  readonly nodeRun: FlowGraphNodeRun | null
  /**
   * flowgate.v02.0053 T#5: the card's document lines as the server keeps them (T#1 — made when the card
   * is placed), in its order; empty on a Node without them (a document / sequence Node).
   */
  readonly cardDocuments: readonly InspectorCardDocument[]
  /** Graph definition node type (`document` | `gate` | `custom`), or null. */
  readonly nodeType: string | null
  readonly rootBound: boolean
  readonly ownership: string | null
  readonly viewMode: string | null
  readonly readOnly: InspectorReadOnly
}

const CARD_KIND_BY_DOC_TYPE: ReadonlyMap<string, InspectorCardKind> = new Map(
  Object.entries(INSPECTOR_CARD_DOC_TYPES).flatMap(([kind, types]) => types.map((type) => [type, kind as InspectorCardKind])),
)

/** Card kind of a document type; unknown or missing types are `other`. */
export function inspectorCardKind(docType: string | null | undefined): InspectorCardKind {
  if (!docType) return 'other'
  return CARD_KIND_BY_DOC_TYPE.get(docType.trim().toUpperCase()) ?? 'other'
}

/** Read-only reason: an ownership conflict wins over a Graph revision view (D#1 §4.3 / §8.3). */
export function inspectorReadOnly(ownership: string | null | undefined, viewMode: string | null | undefined): InspectorReadOnly {
  if (ownership === 'conflict') return 'conflict'
  if (viewMode === 'revision') return 'revision'
  return null
}

/** Selection context -> InspectorTarget; null when nothing is selected. */
export function toInspectorTarget(selection: FlowGraphSelection | null | undefined): InspectorTarget | null {
  if (!selection) return null
  const { graph, node } = selection
  const document = node.document ?? null
  const docType = document?.typeCode ?? node.definition?.docType ?? node.docType ?? null
  const ownership = graph.ownership ?? null
  const viewMode = graph.viewMode ?? null
  return {
    projectId: selection.projectId,
    flowId: graph.flowId,
    nodeId: node.id,
    nodeKind: node.kind,
    docId: document?.docId ?? null,
    docType,
    cardKind: inspectorCardKind(docType),
    title: node.title,
    canvasState: node.state ?? null,
    docReviewStatus: document?.docReviewStatus ?? null,
    docStatus: document?.status ?? null,
    supersedes: document?.supersedes ?? [],
    slot: node.slot ?? null,
    nodeRun: node.nodeRun ?? null,
    cardDocuments: node.cardDocuments ?? [],
    nodeType: node.definition?.nodeType ?? null,
    rootBound: node.definition?.rootBound === true,
    ownership,
    viewMode,
    readOnly: inspectorReadOnly(ownership, viewMode),
  }
}
