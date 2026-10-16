// Project domain model owned by features/project (WP0005 T#1 §2). Transport snake_case
// from `/api/v1/projects*` never spreads past api.ts's normalizer — every component/store
// in this feature (and any future consumer of the public surface) only ever sees this shape.

export interface ProjectModule {
  name: string
  title: string
}

export interface Project {
  id: string
  name: string
  description: string
  color: string | null
  // Archive/active flag (backend `is_active`). This is NOT "is this the current project" —
  // that meaning lives entirely in currentProjectId/currentProjectStorage (NR0003 §5 row
  // "is_active", T#1 §2 "is_active는 archive/active 여부이고 current project 의미가 아님").
  isActive: boolean
  createdAt: string | null
  // "최종 수정" 의미만 허용된다 — 최근 사용/최근 열기 의미로 쓰지 않는다 (D0004 §2.3).
  updatedAt: string | null
  modules: ProjectModule[]
}

export interface CreateProjectInput {
  name: string
  // Required at the v0.2 frontend contract boundary (T0012 §3.3/§3.1) — the v0.1 create
  // screen never treated this as optional, and the backend's own ability to slugify a
  // missing project_id is a backend capability only, not grounds to relax this contract.
  id: string
  description?: string
  color?: string
}

// ── Group tree (flowgate.v02.0023) ───────────────────────────────────────────────────
// flowgate.v02.0023 — the project's group tree as `/api/v1/projects/{id}/groups/tree`
// returns it: ONE flat node list in server order, linked by parentId. The node kinds keep
// their v0.1 storage names on purpose (this is the transport model, shared by every v0.2
// screen that reads the tree); what each kind MEANS on screen is the consuming feature's
// call — the Designer Project Explorer reads `module` as a v0.2 Group and `group` as a
// v0.2 Flow, and never shows `document` (a v0.2 Node).
export type GroupTreeNodeKind = 'project' | 'module' | 'group' | 'subgroup' | 'orphan' | 'document'

export interface GroupTreeNode {
  id: string
  parentId: string | null
  kind: GroupTreeNodeKind
  /** Server key/label (for a module, its key; for a group, its display name). */
  label: string
  /** Server display title, when the node has one separate from `label`. */
  title: string | null
  /** v0.1 group number (e.g. "0023"), when the node carries one. */
  number: string | null
}

export interface FetchGroupTreeOptions {
  /** Read a fresh tree instead of answering from the store cache / joining an older read. */
  force?: boolean
  /** Keep final-approved/discarded groups. Defaults to false (the pruned variant). */
  includeTerminal?: boolean
}

// ── Flow file context (flowgate.v02.0024 T0005) ─────────────────────────────────────────
// Read models for the Designer File Explorer. A Flow is backed by a group worktree, so
// `groupId` is the key every server Git/file contract understands. Like Project above, the
// server's snake_case never leaves api.ts.

/** The active Flow a Designer hands to Flow-scoped views. */
export interface FlowRef {
  groupId: string
  name: string
  branch?: string | null
}

export interface FlowFileNode {
  id: string
  parentId: string | null
  type: 'folder' | 'file'
  name: string
  label: string
  path: string
  permissions: string[]
}

/** The Flow worktree tree: HEAD tree plus untracked files/empty folders, readable nodes only. */
export interface FlowFileTree {
  branch: string
  commit: string | null
  nodes: FlowFileNode[]
}

export interface ProjectGitSlot {
  groupId: string
  branch: string | null
  status: string
  // True only when the server says this slot still has a live, writable worktree. A server
  // that omits the field reads as not writable.
  writable: boolean
}

/**
 * S1 planned finalize of a held (waiting) group (flowgate.v02.0033-D §4.6). Present only when
 * the server sends the S1 fields; `action` / `targetBranch` may still be null (no plan).
 */
export interface ProjectGitPendingPlan {
  action: string | null
  targetBranch: string | null
}

/** One git/status `pending` row (flowgate.v02.0033-D §4.3). */
export interface ProjectGitPendingRow {
  groupId: string
  status: string
  mergeId: number | null
  // The group's final approval (AC) document.
  acDocId: string | null
  reviewState: string | null
  // A conflict tied to a final approval in progress; a new finalize is refused meanwhile.
  finalApprovalBound: boolean
  // null = the server does not send the S1 fields at all (stage 1), which is not the same
  // as an S1 row without a plan. `default_action` is the project default, never read here.
  plan: ProjectGitPendingPlan | null
}

/** Tracked changes of the Base checkout. `files` is the full path list (no status letter). */
export interface ProjectGitBaseDirty {
  files: string[]
  // A merge is stopped on the Base checkout: these changes are that merge, not stray edits.
  mergeInProgress: boolean
  // A Base cleanup AI run owns the checkout right now.
  aiRun: boolean
}

/** Untracked (new) files of the Base checkout. */
export interface ProjectGitBaseUntracked {
  // Capped by the server; only a part of them when `truncated`.
  files: string[]
  truncated: boolean
  // S2 total of new files. null = the server does not send it (stage 1). The stage-1
  // `count` is the capped list length and is deliberately not carried.
  total: number | null
}

export interface ProjectGitStatus {
  enabled: boolean
  // The project's configured Base branch exactly as the server reports it, or null when it
  // is not configured — including when the server only reports its own main-branch fallback
  // (base_branch_configured is not true). Never defaulted on the client (0024-D §12).
  baseBranch: string | null
  slots: ProjectGitSlot[]
  // flowgate.v02.0033: what the Designer Git part reads. Additive only.
  pending: ProjectGitPendingRow[]
  baseDirty: ProjectGitBaseDirty
  baseUntracked: ProjectGitBaseUntracked
}

export interface FlowChangeEntry {
  path: string
  // Raw git letter from the server: M / A / D / R / T / ... and '?' for untracked.
  status: string
}

export interface FlowChangeSet {
  groupId: string
  branch: string
  commit: string | null
  // The branch the server actually compared against, or null when the answer did not say.
  baseBranch: string | null
  changes: FlowChangeEntry[]
}

export interface FlowDiffSide {
  exists: boolean
  binary: boolean
  truncated: boolean
  size: number
  content: string | null
}

export interface FlowFileDiff {
  groupId: string
  branch: string
  commit: string | null
  baseBranch: string | null
  mergeBase: string | null
  path: string
  status: 'M' | 'A' | 'D'
  old: FlowDiffSide
  new: FlowDiffSide
}

/** Whether an AI run currently owns the Flow's group. */
export interface FlowAiActivity {
  active: boolean
  // flowgate.v02.0033: which run, its server status and action scope (null when idle).
  runId: string | null
  status: string | null
  actionScope: string | null
}

/**
 * Old/new content of one Base checkout file (HEAD blob vs working tree,
 * GET /projects/{pid}/git/diff). Same sides as FlowFileDiff (flowgate.v02.0033).
 */
export interface BaseFileDiff {
  branch: string | null
  commit: string | null
  path: string
  status: 'M' | 'A' | 'D'
  old: FlowDiffSide
  new: FlowDiffSide
}

/** Old/new content of one file of a frozen merge review candidate (review-diff). */
export interface MergeReviewFileDiff {
  groupId: string
  mergeId: number
  path: string
  status: 'M' | 'A' | 'D'
  old: FlowDiffSide
  new: FlowDiffSide
}

// ── Recent items and Group / Flow creation (flowgate.v02.0043) ─────────────────────────
// What the Designer File menu needs from this feature: the 최근 Project / Group / Flow lists
// (recentStorage.ts) and the two creates the menu's 새 Group / 새 Flow dialogs call. Ids follow
// the group tree: a Group id is its tree node id (`module:<project>:<name>`), a Flow id is the
// v0.1 group id.

/** A Project opened with "열기". */
export interface RecentProject {
  id: string
  name: string
}

/** A Group (v0.1 module) opened in the Designer, with the Project it belongs to. */
export interface RecentGroup {
  id: string
  name: string
  projectId: string
}

/** A Flow (v0.1 group) opened in the Designer; `groupId` is its Group's tree node id. */
export interface RecentFlow {
  id: string
  name: string
  projectId: string
  groupId: string
}

/** POST /projects/{id}/modules input: `name` is the module key ([a-z0-9_-]), `title` the display name. */
export interface CreateGroupInput {
  name: string
  title: string
}

/** The created Group as the Designer sees it: `id` is the group tree node id. */
export interface CreatedGroup {
  id: string
  name: string
  title: string
}

/** POST /groups input; `moduleName` is the owning Group's module key. */
export interface CreateFlowInput {
  projectId: string
  moduleName: string
  title: string
}

/** The created Flow: `id` is the v0.1 group id the server reserved. */
export interface CreatedFlow {
  id: string
  title: string
}

// ── Flow Canvas (flowgate.v02.0045 S1+S2) ────────────────────────────────────────────
// The Canvas read / layout save contract of P0008 §2·§3·§4, camelCase. kind / state /
// ownership / identitySource / origin are open keys: a value this build does not know is
// passed through untouched and the Canvas falls back to its default look (P0008 §1.5).

/** P0008 §2.1 — `unassigned` | `v01` | `graph` | `conflict` (open key). */
export interface FlowCanvasOwnership {
  state: string
  sequenceRootDocId: string | null
  graphRevisionNo: number | null
  assignedAt: string | null
}

export interface FlowCanvasPosition {
  x: number
  y: number
  /** `saved` = stored layout, `auto` = server auto placement (never stored). */
  source: string
}

export interface FlowCanvasDocument {
  docId: string
  typeCode: string | null
  seq: number | null
  title: string | null
  docReviewStatus: string | null
  status: string | null
  supersedes: string[]
}

export interface FlowCanvasSlot {
  itemId: number
  itemSeq: number | null
  order: number
  type: string
  label: string | null
  derivedStatus: string | null
  protected: boolean
  sourceWpCardId: string | null
}

export interface FlowCanvasNode {
  /** Node identity (P0008 §1.6): opaque, stable across reads and v0.1 edits. */
  nodeKey: string
  identitySource: string
  origin: string
  kind: string
  docType: string | null
  title: string
  state: string | null
  position: FlowCanvasPosition
  editable: { move: boolean; structure: boolean }
  document: FlowCanvasDocument | null
  slot: FlowCanvasSlot | null
  /** flowgate.v02.0045 S3: the Graph definition of a draft / revision node (P0008 §2.6). */
  graphNode?: FlowCanvasGraphNode | null
  /** flowgate.v02.0048 T#1: the run state of a Graph node (P0008 §2.6 `node_run`); absent elsewhere. */
  nodeRun?: FlowCanvasNodeRun | null
  /** flowgate.v02.0053 T#5: the card's document lines (`card_documents`) of a Graph / draft node; absent elsewhere. */
  cardDocuments?: FlowCanvasCardDocument[]
}

/**
 * flowgate.v02.0053 T#1 `card_documents` line: one document a card holds (made when the card is placed).
 * `slot` / `author` are open keys as the server sends them; `docId` is null when the line's document is gone.
 */
export interface FlowCanvasCardDocument {
  /** requirement · delegation · plan · deploy · instruction · scenario · report. */
  slot: string
  docType: string | null
  /**
   * `human` (a person writes it) | `run` (a run fills it) | `instruction_ai_or_human` (flowgate.v02.0056 O2: a
   * run card's instruction line — its instruction AI or a person writes it).
   */
  author: string | null
  docId: string | null
  title: string | null
  status: string | null
  docReviewStatus: string | null
  /**
   * flowgate.v02.0056 F2-2 (O3 0009-TR §4-1): the line's document has a body after its front matter (false
   * without a document); absent when not sent. M and WP lines are always true (the v0.1 approval rule).
   */
  hasBody?: boolean
  readonly bodyState?: string | null
  /** flowgate.v02.0056 F2-2 (O3 §4-1): AI reviews of the line's document (`ai_review_history` length); absent when not sent. */
  reviewCount?: number
  /** flowgate.v02.0056 F2-2 (O3 §4-1): rejections with a reason (what the history window shows); absent when not sent. */
  rejectionCount?: number
  /** flowgate.v02.0056 F2-2 (O3 §4-1): reviewCount + rejectionCount — the history button's number; absent when not sent. */
  historyCount?: number
}

/** P0008 §2.6 `node_run`: what the Inspector shows of a Graph node's run (status kept as the server sends it). */
export interface FlowCanvasNodeRun {
  /** Existing node_run.dispatch.error_code; no result is still an open card. */
  dispatchErrorCode?: string | null
  status: string | null
  openedAt: string | null
  finishedAt: string | null
  resultDocId: string | null
  /** flowgate.v02.0056 F2-1 (O1 0005-TR §4-2): the card waits for its approval; absent when not sent. */
  awaitingApproval?: boolean
  /** flowgate.v02.0056 F2-1 (O2 0007-TR §4-2): `instruction` | `result` | null; absent when not sent. */
  stage?: string | null
  /** flowgate.v02.0056 F2-1 (O2 0007-TR §4-2): the card run's instruction document; absent when not sent. */
  instructionDocId?: string | null
  /**
   * flowgate.v02.0061 T#14: the node's attempt result integration (`pending` | `integrating` | `integrated` |
   * `conflict` | `failed`, open key) and why; absent when not sent, null when the attempt has none.
   */
  integrationStatus?: string | null
  integrationReason?: string | null
  /** flowgate.v02.0061 T#14: the card is held (T#11 Hold); absent when not sent. */
  hold?: boolean
  /** flowgate.v02.0063: the card's AI asked a question still unanswered; absent when not sent. */
  question?: boolean
  /** flowgate.v02.0061 T#14: the node attempt's running / requested AI executions; absent when not sent. */
  runningExecutionCount?: number
  requestedExecutionCount?: number
  /** flowgate.v02.0061 T#15: the node's current attempt (active, else last ended), null when none; absent when not sent. */
  attemptId?: string | null
}

/** P0008 §2.6 `graph_node`: what a Graph node is, besides where it is drawn. */
export interface FlowCanvasGraphNode {
  /** `document` | `gate` | `custom` (P0008 §5.1). */
  nodeType: string
  label: string
  paletteItemId: string | null
  assignee: FlowGraphAssignee | null
  /** The start node the Flow's existing root R / B document is bound to (D0004 §3.7). */
  rootBound: boolean
}

export interface FlowGraphAssignee {
  kind: string
  userId: string | null
  providerId: string | null
}

/** P0008 §3 `view`: which Graph definition the Canvas shows. */
export interface FlowCanvasView {
  /** `revision` | `draft`. */
  mode: string
  revisionNo: number | null
  draftVersion: number | null
}

/** P0008 §3 `graph` block. */
export interface FlowCanvasGraphBlock {
  draft: {
    draftVersion: number
    baseRevisionNo: number | null
    updatedAt: string | null
    updatedBy: string | null
    confirmable: boolean
    /** Why the draft cannot be confirmed: `flow_owned_by_v01` | `flow_ownership_conflict`. */
    blockedReason: string | null
  } | null
  latestRevisionNo: number | null
  rootBinding: { nodeKey: string; docId: string | null; boundAtRevisionNo: number | null } | null
}

export interface FlowCanvasEdge {
  edgeId: string
  /** `execution` decides what runs next; `reference` only shows a document relation. */
  edgeClass: string
  origin: string
  source: string
  target: string
  relation: string | null
  editable: boolean
}

export interface FlowCanvasExternalRelation {
  nodeKey: string
  relation: string
  otherDocId: string
  otherFlowId: string | null
  otherTitle: string | null
}

export interface FlowCanvasViewport {
  x: number
  y: number
  zoom: number
}

/**
 * flowgate.v02.0061 TR0031 rev1: P0008 S1-3 `run` — the run the Canvas read shows (a Graph Flow's running / paused /
 * stopping run on whatever revision it pinned, else its latest run on the latest revision), whatever the view.
 */
export interface FlowCanvasRun {
  runId: string
  status: string
  revisionNo: number | null
  /** flowgate.v02.0061 T#15: the run's event sequence (grows with every run event); absent when not sent. */
  lastEventSeq?: number
}

/** GET /api/v1/flows/{flowId}/canvas. */
export interface FlowCanvas {
  flowId: string
  projectId: string | null
  ownership: FlowCanvasOwnership
  /** `documents` | `sequence` | `graph` (open key). */
  projection: string
  nodes: FlowCanvasNode[]
  edges: FlowCanvasEdge[]
  externalRelations: FlowCanvasExternalRelation[]
  /** The requesting user's saved viewport, or null (fit all nodes). */
  viewport: FlowCanvasViewport | null
  identity: { sequenceTag: string | null; rematched: boolean; retiredNodeKeys: string[] }
  collapsed: { applied: string[]; ignoredKeys: string[]; hiddenCounts: Record<string, number> }
  /** flowgate.v02.0045 S3: the shown Graph definition, or null (no Graph nodes drawn). */
  view?: FlowCanvasView | null
  graph?: FlowCanvasGraphBlock | null
  /** flowgate.v02.0061 TR0031 rev1: the run block — null = the read shows no run; absent = not sent. */
  run?: FlowCanvasRun | null
}

/** PUT /api/v1/flows/{flowId}/canvas/layout input. `viewport: null` = unchanged. */
export interface FlowCanvasLayoutInput {
  positions: Array<{ nodeKey: string; x: number; y: number }>
  viewport: FlowCanvasViewport | null
}

export interface FlowCanvasLayoutResult {
  savedPositions: number
  viewportSaved: boolean
  /** Keys the server did not store (`retired` / `unknown_node_key`): re-read the Canvas. */
  ignored: Array<{ nodeKey: string; reason: string }>
}

// ── Graph definition (flowgate.v02.0045 S3+S4) ───────────────────────────────────────
// P0008 §5 / §6: draft save (whole replacement), validation and confirmation. Node / edge keys
// are opaque; a new node or edge carries a `tmp_` client key until the save answers its key.

/** P0008 §5.1 node of a draft save. Exactly one of nodeKey / clientKey. */
export interface FlowGraphDraftNodeInput {
  nodeKey: string | null
  clientKey: string | null
  nodeType: string
  docType: string | null
  paletteItemId: string | null
  label: string
  assignee: FlowGraphAssignee | null
}

/** P0008 §5.1 edge; source / target are node keys or client keys of the same request. */
export interface FlowGraphDraftEdgeInput {
  edgeId: string | null
  clientKey: string | null
  source: string
  target: string
}

export interface FlowGraphDraftInput {
  /** The draft version this save replaces (0 = the first save). */
  draftVersion: number
  nodes: FlowGraphDraftNodeInput[]
  edges: FlowGraphDraftEdgeInput[]
}

export interface FlowGraphDraftSaveResult {
  draftVersion: number
  /** client key -> issued key, for nodes and edges alike. */
  keyMap: Record<string, string>
  removedNodeKeys: string[]
  removedEdgeIds: string[]
  /** flowgate.v02.0053 T#5: node key -> the saved draft node's card lines (`card_documents`), when answered. */
  cardDocuments?: Record<string, FlowCanvasCardDocument[]>
}

/** P0008 S3-5 issue: `code` / `structure` are open keys, `message` is display text. */
export interface FlowGraphIssue {
  code: string
  ruleRef: string | null
  structure: string | null
  nodeKeys: string[]
  edgeIds: string[]
  message: string
}

export interface FlowGraphValidation {
  draftVersion: number
  confirmable: boolean
  issues: FlowGraphIssue[]
  rootBindingPreview: { status: string; nodeKey: string | null; docId: string | null; reason: string | null } | null
}

export interface FlowGraphConfirmResult {
  revisionNo: number
  ownership: FlowCanvasOwnership
  rootBinding: { status: string; nodeKey: string | null; docId: string | null; boundAtRevisionNo: number | null }
  retiredNodeKeys: string[]
}

export interface FlowGraphDefinitionNode {
  nodeKey: string
  nodeType: string
  docType: string | null
  paletteItemId: string | null
  label: string
  assignee: FlowGraphAssignee | null
  /** flowgate.v02.0061 T#15: the card's Hold as the Graph read tells it (T#11 `hold`); absent = not told. */
  hold?: boolean
}

export interface FlowGraphDefinitionEdge {
  edgeId: string
  source: string
  target: string
}

/** GET /api/v1/flows/{flowId}/graph (P0008 §5.2). */
export interface FlowGraphDefinition {
  flowId: string
  ownership: FlowCanvasOwnership
  draft: { draftVersion: number; nodes: FlowGraphDefinitionNode[]; edges: FlowGraphDefinitionEdge[] } | null
  latestRevision: { revisionNo: number; nodes: FlowGraphDefinitionNode[]; edges: FlowGraphDefinitionEdge[] } | null
  policies: Record<string, string>
}

// flowgate.v02.0053 T#9 — the Graph run of a Flow (P0008 §7 S5: start / current / stop / resume). The top
// band's 다음 단계 · 전체 실행 · 중지 drive it; the Canvas shows the node states it causes.
// flowgate.v02.0061 T#14 — one run per Flow (`running` | `paused` | `stopping` | `idle`): the step / full command,
// Pause, Stop and Resume (T#2 / T#7), with the idle reason, the execution counts and each node's integration / Hold.

/** One node of a run (P0008 S5-4 `node_runs[]`). */
export interface FlowRunNode {
  nodeKey: string
  /** `pending` | `open` | `review` | `done` | `rejected` … (open key, as sent). */
  status: string
  resultDocId: string | null
  /** flowgate.v02.0056 F2-1 (O1 0005-TR §4-2): the card waits for its approval; absent when not sent. */
  awaitingApproval?: boolean
  /** flowgate.v02.0056 F2-1 (O2 0007-TR §4-2): `instruction` | `result` | null; absent when not sent. */
  stage?: string | null
  /** flowgate.v02.0061 T#14: the node attempt's result integration status (open key) or null; absent when not sent. */
  integrationStatus?: string | null
  /** flowgate.v02.0061 T#14: the card is held (T#11); absent when not sent. */
  hold?: boolean
  /** flowgate.v02.0063: the card's AI asked a question still unanswered; absent when not sent. */
  question?: boolean
  /** flowgate.v02.0061 T#14: the node attempt's running / requested AI executions; absent when not sent. */
  runningExecutionCount?: number
  requestedExecutionCount?: number
  /** flowgate.v02.0061 T#15: the node's current attempt (active, else last ended), null when none; absent when not sent. */
  attemptId?: string | null
}

/** A Graph run (P0008 S5-4). */
export interface FlowRun {
  runId: string
  flowId: string
  revisionNo: number | null
  /** flowgate.v02.0061 T#14: `running` | `paused` | `stopping` | `idle` (open key, as sent). */
  status: string
  /** flowgate.v02.0061 T#14: the run's last command, `step` | `full` (open key), or null when not sent. */
  mode: string | null
  /**
   * flowgate.v02.0061 T#14: why an idle run is idle — `completed` | `no_targets` | `waiting_approval` |
   * `blocked_rejected` | `error` | `stopped` | `merge_conflict` | `hold` | `step_done` (open key) — or null.
   */
  idleReason: string | null
  /** flowgate.v02.0061 T#14: the run's running / requested AI executions and the Flow's limit (null: not sent). */
  runningExecutionCount: number
  requestedExecutionCount: number
  executionLimit: number | null
  openNodeKeys: string[]
  /** Present when the server answered with the nodes (start / current / gate decision). */
  nodeRuns: FlowRunNode[] | null
  lastEventSeq: number
  /**
   * flowgate.v02.0056 F2-1 (O1 0005-TR §4-2): cards approved while the run was stopped whose successors wait for
   * the run to go on ("멈춤 · 다음 카드 N개 대기"), and their node keys (sorted). 0 / [] when not sent.
   */
  pendingPropagationCount: number
  pendingNodeKeys: string[]
}

/**
 * flowgate.v02.0061 T#15 — what a Graph Time Machine did (T#13 `time_machine`): the operation, the server's impact
 * (the node and every node below it that it took back) and what changed. Lists are [] and counts 0 when not sent.
 */
export interface FlowRunTimeMachine {
  operationId: string | null
  /** `completed` (open key, as sent), or null. */
  operationStatus: string | null
  nodeKey: string
  /** The impact's node keys (the selected node and its downstream, by the server's execution history). */
  impactNodeKeys: string[]
  /** The nodes set back to pending. */
  resetNodeKeys: string[]
  /** The documents opened again. */
  reopenedDocumentIds: string[]
  /** The integration commits reverted on the group branch. */
  revertedCommitCount: number
  /** The attempts invalidated. */
  invalidatedAttemptCount: number
  revokedTokenCount: number
  /** The run's idle reason after it, or null. */
  idleReason: string | null
}

/** flowgate.v02.0061 T#15 — one reason a Graph Time Machine was refused (409 `graph_time_machine_blocked`). */
export interface FlowRunTimeMachineReason {
  code: string
  /** The server's sentence (x-locale), or ''. */
  message: string
}

/** flowgate.v02.0061 T#15 — a refused Graph Time Machine's assessment as sent in the refusal's params. */
export interface FlowRunTimeMachineRefusal {
  reasons: FlowRunTimeMachineReason[]
  /** The impact's node keys the assessment found ([] when not sent). */
  impactNodeKeys: string[]
}

/** flowgate.v02.0056 F2-1 (O1 0005-TR §4-1): what a card approval did. */
export interface FlowRunApproval {
  nodeKey: string
  /** The card's documents in the order they are approved (instruction first, the result last). */
  targetDocIds: string[]
  /** The documents this approval approved. */
  approvedDocIds: string[]
}
