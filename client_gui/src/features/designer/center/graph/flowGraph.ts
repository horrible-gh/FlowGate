// FlowGate Graph Model. The one Graph shape the Center
// session keeps per Flow. It knows nothing about the renderer: Vue Flow node/edge objects are
// derived from it one way (canvas/vueFlowMapping.ts) and never written back.
//
// kind / state are open domain keys, not display text — canvas/flowNodeKinds.ts maps the
// known ones and shows anything else with the default look. Node size, per-Node detail data,
// Edge condition and Flow metadata are deliberately absent until the server Graph contract
// needs them (0003-D DEFERRED); they are added as optional fields.
//
// S1+S2 ("Graph model extensions"): optional meaning fields (edge class,
// ownership) and layout fields (position source, movable, saved viewport) from the server
// Canvas read. A source that does not know them (the sample source) simply leaves them out.
//
// S3+S4 ("Separate semantic and layout fields"): meaning fields of the
// Graph definition — a node's `definition` (what the draft / revision node is) and
// `structureEditable`, an edge's `editable`, and the Flow's `draft` state. Layout fields stay
// x / y / positionSource / movable / viewport.
//
// the per-Node detail the Inspector needs — the bound
// document, the v0.1 sequence slot and the Graph node run — as optional meaning fields, plus the
// selection context (FlowGraphSelection) the Center session hands to the bottom Inspector. The
// Canvas card does not read them, so the Canvas look does not change.
//
// a Graph / draft node's card document lines (`cardDocuments`), for the
// Inspector's documents tab; also a meaning field the Canvas card does not read.
//
// the Canvas read's `projection` (layout field), so auto-layout follows the server's
// auto placement of that projection; with it the v0.1 sequence root document and how many Nodes the read
// left out (collapse), so auto-layout knows when the shown Graph is not the server's whole input.
//
// the node run also carries whether the card waits for its approval and the AI
// card's step (O1 / O2); the Canvas card now reads the node run for its state chip (graph/cardStateView.ts).
//
// the card Holds the Graph read tells (`holds`), so a card the run has no node run of shows its
// Hold too.
//
// a Graph node's card title state (`titleState`), the base of the card's inline title edit.

import type { RoleRunFields } from '@/features/project'

export interface FlowGraphNode {
  /** Unique within the Graph — the server node identity for a server Graph. */
  readonly id: string
  /** Domain key, e.g. 'requirement' / 'task'. Unknown keys render with the default look. */
  readonly kind: string
  readonly title: string
  /** Optional domain key, display only. */
  readonly state?: string | null
  /** Display only: the document type the Node stands for, if any. */
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
  /** Meaning: the card's document lines (Graph / draft nodes only). */
  readonly cardDocuments?: readonly FlowGraphCardDocument[] | null
  /**
   * Meaning: the card's own title state (`title` above already shows it). Only a v0.2 Graph node
   * (document / gate / custom) has one; null / absent elsewhere and from an older server — its title is not editable.
   */
  readonly titleState?: FlowGraphTitleState | null
  /**
   * the `tmp_` key the node had until its draft save answered its server key (draft remapGraph).
   * The Canvas keeps dragging a card held through that rename under the name it grabbed it with. Absent otherwise.
   */
  readonly renamedFrom?: string
}

/**
 * a card title state as the server sent it, one bundle. The
 * screen takes a bundle only when its `version` is above the one it shows (graph/nodeTitle.ts).
 */
export interface FlowGraphTitleState {
  readonly title: string | null
  readonly titleSource: string | null
  readonly version: number
}

/** One document line of a card (the server makes them when the card is placed, the earlier change the earlier implementation). */
export interface FlowGraphCardDocument {
  /** requirement · delegation · plan · deploy · instruction · scenario · report (open key). */
  readonly slot: string
  readonly docType: string | null
  /**
   * `human` | `run` | `instruction_ai_or_human` (the earlier change O2: the instruction line of a run card — its
   * instruction AI or a person writes it). Open key, as sent.
   */
  readonly author: string | null
  /** null when the line's document is gone. */
  readonly docId: string | null
  readonly title: string | null
  readonly status: string | null
  readonly docReviewStatus: string | null
  /**
   * the line's document has a body (false without a document); absent
   * when the server did not send it. M and WP lines are always true.
   */
  readonly hasBody?: boolean
  readonly bodyState?: string | null
  /** AI reviews of the line's document; absent when not sent. */
  readonly reviewCount?: number
  /** rejections with a reason; absent when not sent. */
  readonly rejectionCount?: number
  /** reviewCount + rejectionCount (the history button's number); absent when not sent. */
  readonly historyCount?: number
}

/**
 * only these known body states warn (the server's `body_state`); an older server sends none and
 * a documentless line never warns. The same rule as features/document `lineWarns` and the Inspector parts'
 * (`parts/slotStateView.ts`): the Designer Center imports features/document nowhere, so it keeps its own copy.
 */
export const UNREADABLE_BODY_STATES: ReadonlySet<string> = new Set([
  'file_path_missing', 'file_not_found', 'path_unresolved', 'read_error',
])

export function lineWarns(line: { readonly docId?: string | null; readonly bodyState?: string | null }): boolean {
  return line.docId != null && typeof line.bodyState === 'string' && UNREADABLE_BODY_STATES.has(line.bodyState)
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

export interface FlowGraphNodeRun extends RoleRunFields {
  readonly intervention?: { readonly kind: string; readonly reason: string | null } | null
  readonly gate?: import('@/features/document').RunGateState | null
  readonly dispatchErrorCode?: string | null
  /** the failed AI call's short cause (node_run.dispatch.error_detail); absent when not sent. */
  readonly dispatchErrorDetail?: string | null
  readonly status: string | null
  readonly openedAt: string | null
  readonly finishedAt: string | null
  readonly resultDocId: string | null
  /**
   * the card waits for its approval (a review node whose result no
   * AI works on any more). Absent = false.
   */
  readonly awaitingApproval?: boolean
  /** `instruction` | `result` | null — the AI card's current step. */
  readonly stage?: string | null
  /** the instruction document this card run writes (wrote), or null. */
  readonly instructionDocId?: string | null
  /**
   * the node attempt's result integration (`pending` | `integrating` | `integrated` |
   * `conflict` | `failed`, open key) and why, or null. Absent = not sent.
   */
  readonly integrationStatus?: string | null
  readonly integrationReason?: string | null
  /** the card (node key) whose merge this card's merge waits for, or null. Absent = not sent. */
  readonly integrationBlockedBy?: string | null
  /** the card is held (Hold). Absent = false. */
  readonly hold?: boolean
  /** the card's document holds a question still unanswered. Absent = false. */
  readonly question?: boolean
  questionWait?: readonly { seq: number; state: string; code: string | null; message: string | null }[]
  /** the card is running now (the server's card running lock, `node_running`). Absent = not sent. */
  readonly nodeRunning?: boolean
  /** the node attempt's running / requested AI executions. Absent = 0. */
  readonly runningExecutionCount?: number
  readonly requestedExecutionCount?: number
  /** the node's current attempt (null: none). Absent = not sent. */
  readonly attemptId?: string | null
  /** (B-2): why the result was not approved automatically while the card awaits its approval (the latest B-1 reason, e.g. `approval_refused`, `review_not_passed:review_count_exhausted`), or null; absent when not sent. */
  readonly reviewExhaustionApproval?: { review_count?: number | null; stage?: string | null; doc_id?: string | null; revision_no?: number | null } | null
  readonly approvalWaitReason?: string | null
  /** v02gui.0015 T2: the earlier attempts' uncommitted changes the attempt did not take, null when none; absent when not sent. */
  readonly uncarriedChanges?: import('@/features/project/types').UncarriedChanges | null
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
   * Layout: how the server laid the Nodes out — `documents` | `sequence` | `graph`
   * (P0008 §3 `projection`, open key); auto-layout places them by the same rules. Absent: unknown.
   */
  readonly projection?: string | null
  /**
   * Meaning: the v0.1 sequence root document (the plan
   * `ownership.sequence_root_doc_id`), or null / absent. The `sequence` projection draws the live end of
   * its supersede chain first.
   */
  readonly sequenceRootDocId?: string | null
  /**
   * Layout: how many Nodes the read left out (collapse); the server laid
   * them out with the others before leaving them out. Absent: none.
   */
  readonly hiddenNodeCount?: number
  /**
   * Meaning: the run the Canvas read shows, null when it shows none (no run, or not a
   * Graph Flow). Absent: the source says nothing about runs (a sample source, an older server).
   */
  readonly run?: FlowGraphRun | null
  /**
   * Meaning: the card Holds the Graph read (`hold`) tells with this Graph, by
   * node key — also of cards the run has no node run of. Absent: not read (a sample source, or that read failed).
   */
  readonly holds?: Readonly<Record<string, boolean>>
}

/**
 * the run the Canvas read shows with the Graph (S1-3 `run`) — its status keeps the
 * run lock while the run read is pending or failed.
 */
export interface FlowGraphRun {
  readonly runId: string
  readonly status: string
  /**
   * the revision the run runs (null: not sent) and its event sequence (absent: not sent) — with
   * the node runs' attempts, the Time Machine confirmation's basis when the run read is pending or failed.
   */
  readonly revisionNo?: number | null
  readonly lastEventSeq?: number
  /**
   * the run band's facts of the same read (FlowRunInfo's names), each absent when not sent —
   * a Canvas read newer than the run read tells the band as of itself, not beside the older run read's facts.
   */
  readonly mode?: string | null
  readonly idleReason?: string | null
  readonly runningExecutionCount?: number
  readonly requestedExecutionCount?: number
  readonly executionLimit?: number | null
  readonly pendingPropagationCount?: number
  readonly pendingNodeKeys?: readonly string[]
  /** (W1): the same read's automatic Q&A responder fact (FlowRunInfo's), absent when not sent. */
  readonly qaResponding?: boolean
}

/** the selected Node with the Flow and project it belongs to (Center session -> Inspector). */
export interface FlowGraphSelection {
  readonly projectId: string
  readonly graph: FlowGraph
  readonly node: FlowGraphNode
  /**
   * why the Flow's structure and card settings cannot change now — `run` while its run is
   * running, paused or stopping (center/run/flowRunLock.ts); the earlier implementation: `unknown` while its run status is not
   * confirmed (being read, or the read failed). Absent / null = not locked.
   */
  readonly editLock?: 'run' | 'unknown' | 'archived' | null
  readonly flowReadOnly?: boolean
  /**
   * whether the card's own title may be edited now (graph/nodeTitle.ts nodeTitleEdit) and, when it
   * may not because the card runs, `lock: 'running'`. Absent / null = not editable (the source saves no titles).
   */
  readonly titleEdit?: { readonly editable: boolean; readonly lock: 'running' | null } | null
  /**
   * (B-6): the server confirmed that the user may approve the Flow's cards (its run read's
   * `viewer.can_approve`). Absent / false = [승인] is not shown.
   */
  readonly canApprove?: boolean
}
