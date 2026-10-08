// flowgate.v02.0026 — FlowGate Graph Model (0003-D §3.2.1). The one Graph shape the Center
// session keeps per Flow. It knows nothing about the renderer: Vue Flow node/edge objects are
// derived from it one way (canvas/vueFlowMapping.ts) and never written back.
//
// kind / state are open domain keys, not display text — canvas/flowNodeKinds.ts maps the
// known ones and shows anything else with the default look. Node size, per-Node detail data,
// Edge condition and Flow metadata are deliberately absent until the server Graph contract
// needs them (0003-D DEFERRED); they are added as optional fields.
//
// flowgate.v02.0045 S1+S2 (D0004 §2.3 "Graph 모델 확장"): optional meaning fields (edge class,
// ownership) and layout fields (position source, movable, saved viewport) from the server
// Canvas read. A source that does not know them (the sample source) simply leaves them out.
//
// flowgate.v02.0045 S3+S4 (D0004 §2.3 "의미와 레이아웃 필드는 구분"): meaning fields of the
// Graph definition — a node's `definition` (what the draft / revision node is) and
// `structureEditable`, an edge's `editable`, and the Flow's `draft` state. Layout fields stay
// x / y / positionSource / movable / viewport.

export interface FlowGraphNode {
  /** Unique within the Graph — the server node identity for a server Graph. */
  readonly id: string
  /** Domain key, e.g. 'requirement' / 'task'. Unknown keys render with the default look. */
  readonly kind: string
  readonly title: string
  /** Optional domain key, display only. */
  readonly state?: string | null
  /** Free World coordinates — never derived from an order / sequence. */
  readonly x: number
  readonly y: number
  /** Layout: 'saved' = stored position, 'auto' = server auto placement. */
  readonly positionSource?: string | null
  /** Layout: true when the user may drag the Node (its position is then saved). */
  readonly movable?: boolean
  /** Meaning: true for a node of an editable draft (it may be removed or connected). */
  readonly structureEditable?: boolean
  /** Meaning: the Graph definition of a draft / revision node; absent for document / slot nodes. */
  readonly definition?: FlowGraphNodeDefinition | null
}

export interface FlowGraphNodeDefinition {
  /** `document` | `gate` | `custom`. */
  readonly nodeType: string
  readonly docType: string | null
  readonly paletteItemId: string | null
  readonly label: string
  readonly assignee: { readonly kind: string; readonly userId: string | null; readonly providerId: string | null } | null
  /** The start node holding the Flow's existing root document. */
  readonly rootBound?: boolean
}

export interface FlowGraphEdge {
  /** Unique within the Graph. */
  readonly id: string
  /** Source Node id. */
  readonly source: string
  /** Target Node id. */
  readonly target: string
  /** Meaning: 'execution' (decides what runs next) or 'reference' (document relation only). */
  readonly edgeClass?: string | null
  /** Meaning: an execution edge of an editable draft (it may be removed). */
  readonly editable?: boolean
}

/** The Flow's Graph draft as the Canvas shows it (P0008 §3 `graph.draft` / `view`). */
export interface FlowGraphDraftInfo {
  /** Saved draft version; 0 = no draft yet (the first structure edit creates it). */
  readonly version: number
  /** Structure edits (Palette drop, connect, remove) are allowed on the shown Graph. */
  readonly editable: boolean
  /** Ownership allows confirming (P0008 §5.2 `confirmable`); structure is checked by validation. */
  readonly confirmable: boolean
  /** `flow_owned_by_v01` | `flow_ownership_conflict` when ownership forbids confirming. */
  readonly blockedReason: string | null
  /** The revision the draft was copied from (null on an unassigned Flow). */
  readonly baseRevisionNo: number | null
}

export interface FlowGraphViewport {
  readonly x: number
  readonly y: number
  readonly zoom: number
}

export interface FlowGraph {
  /** Same value as FlowRef.groupId of the Flow this Graph belongs to. */
  readonly flowId: string
  readonly nodes: readonly FlowGraphNode[]
  readonly edges: readonly FlowGraphEdge[]
  /** Which engine owns the Flow: 'unassigned' | 'v01' | 'graph' | 'conflict' (open key). */
  readonly ownership?: string | null
  /** Layout: the user's saved Viewport of this Flow, or null (fit all Nodes). */
  readonly viewport?: FlowGraphViewport | null
  /** Meaning: the draft of this Flow, or null when it can have none shown (v0.1, no draft). */
  readonly draft?: FlowGraphDraftInfo | null
  /** Meaning: which Graph definition is shown: 'draft' | 'revision' | null (none). */
  readonly viewMode?: string | null
  /** Meaning: the latest confirmed revision number, or null. */
  readonly latestRevisionNo?: number | null
}
