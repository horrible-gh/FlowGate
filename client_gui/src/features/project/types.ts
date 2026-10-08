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
