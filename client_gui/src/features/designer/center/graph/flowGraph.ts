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
//
// flowgate.v02.0048 T#1 (D#1 §4.1 / §4.2): the per-Node detail the Inspector needs — the bound
// document, the v0.1 sequence slot and the Graph node run — as optional meaning fields, plus the
// selection context (FlowGraphSelection) the Center session hands to the bottom Inspector. The
// Canvas card does not read them, so the Canvas look does not change.
//
// flowgate.v02.0053 T#5: a Graph / draft node's card document lines (`cardDocuments`), for the
// Inspector's documents tab; also a meaning field the Canvas card does not read.
//
// flowgate.v02.0059 T#2: the Canvas read's `projection` (layout field), so 자동 배치 follows the server's
// auto placement of that projection; with it the v0.1 sequence root document and how many Nodes the read
// left out (collapse), so 자동 배치 knows when the shown Graph is not the server's whole input.
//
// flowgate.v02.0056 F2-1: the node run also carries whether the card waits for its approval and the AI
// card's step (O1 / O2); the Canvas card now reads the node run for its state chip (graph/cardStateView.ts).

export interface FlowGraphNode {
  /** Unique within the Graph — the server node identity for a server Graph. */
  readonly id: string
  /** Domain key, e.g. 'requirement' / 'task'. Unknown keys render with the default look. */
  readonly kind: string
  readonly title: string
  /** Optional domain key, display only. */
  readonly state?: string | null
  /** Display only (flowgate.v02.0045 T0019): the document type the Node stands for, if any. */
  readonly docType?: string | null
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
  /** Meaning (0048): the live document bound to the Node, or null / absent (no document). */
  readonly document?: FlowGraphNodeDocument | null
  /** Meaning (0048): the v0.1 sequence slot behind the Node (v0.1 Flows only). */
  readonly slot?: FlowGraphNodeSlot | null
  /** Meaning (0048): the Graph node run state (Graph Flows only). */
  readonly nodeRun?: FlowGraphNodeRun | null
  /** Meaning (flowgate.v02.0053 T#5): the card's document lines (Graph / draft nodes only). */
  readonly cardDocuments?: readonly FlowGraphCardDocument[] | null
}

/** One document line of a card (the server makes them when the card is placed, flowgate.v02.0053 T#1). */
export interface FlowGraphCardDocument {
  /** requirement · delegation · plan · deploy · instruction · scenario · report (open key). */
  readonly slot: string
  readonly docType: string | null
  /**
   * `human` | `run` | `instruction_ai_or_human` (flowgate.v02.0056 O2: the instruction line of a run card — its
   * instruction AI or a person writes it). Open key, as sent.
   */
  readonly author: string | null
  /** null when the line's document is gone. */
  readonly docId: string | null
  readonly title: string | null
  readonly status: string | null
  readonly docReviewStatus: string | null
  /**
   * flowgate.v02.0056 F2-2 (O3 0009-TR §4-1): the line's document has a body (false without a document); absent
   * when the server did not send it. M and WP lines are always true.
   */
  readonly hasBody?: boolean
  /** flowgate.v02.0056 F2-2: AI reviews of the line's document; absent when not sent. */
  readonly reviewCount?: number
  /** flowgate.v02.0056 F2-2: rejections with a reason; absent when not sent. */
  readonly rejectionCount?: number
  /** flowgate.v02.0056 F2-2: reviewCount + rejectionCount (the history button's number); absent when not sent. */
  readonly historyCount?: number
}

export interface FlowGraphNodeDocument {
  readonly docId: string
  readonly typeCode: string | null
  readonly seq: number | null
  readonly title: string | null
  readonly docReviewStatus: string | null
  readonly status: string | null
  /** Ids of the earlier documents this one replaces. */
  readonly supersedes: readonly string[]
}

export interface FlowGraphNodeSlot {
  readonly itemId: number
  readonly itemSeq: number | null
  readonly order: number
  readonly type: string
  readonly label: string | null
  readonly derivedStatus: string | null
  readonly protected: boolean
  readonly sourceWpCardId: string | null
}

export interface FlowGraphNodeRun {
  readonly status: string | null
  readonly openedAt: string | null
  readonly finishedAt: string | null
  readonly resultDocId: string | null
  /**
   * flowgate.v02.0056 F2-1 (O1 0005-TR §4-2): the card waits for its approval (a review node whose result no
   * AI works on any more). Absent = false.
   */
  readonly awaitingApproval?: boolean
  /** flowgate.v02.0056 F2-1 (O2 0007-TR §4-2): `instruction` | `result` | null — the AI card's current step. */
  readonly stage?: string | null
  /** flowgate.v02.0056 F2-1 (O2 0007-TR §4-2): the instruction document this card run writes (wrote), or null. */
  readonly instructionDocId?: string | null
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
  /** Meaning: the revision shown in the revision view (a run may pin an older one), else null. */
  readonly viewRevisionNo?: number | null
  /**
   * Layout (flowgate.v02.0059 T#2): how the server laid the Nodes out — `documents` | `sequence` | `graph`
   * (P0008 §3 `projection`, open key); 자동 배치 places them by the same rules. Absent: unknown.
   */
  readonly projection?: string | null
  /**
   * Meaning (flowgate.v02.0059 T#2): the v0.1 sequence root document (P0008 §2.1
   * `ownership.sequence_root_doc_id`), or null / absent. The `sequence` projection draws the live end of
   * its supersede chain first.
   */
  readonly sequenceRootDocId?: string | null
  /**
   * Layout (flowgate.v02.0059 T#2): how many Nodes the read left out (P0008 §2.5 collapse); the server laid
   * them out with the others before leaving them out. Absent: none.
   */
  readonly hiddenNodeCount?: number
}

/** flowgate.v02.0048 T#1: the selected Node with the Flow and project it belongs to (Center session -> Inspector). */
export interface FlowGraphSelection {
  readonly projectId: string
  readonly graph: FlowGraph
  readonly node: FlowGraphNode
}
