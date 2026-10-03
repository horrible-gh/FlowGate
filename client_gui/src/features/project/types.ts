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

export interface ProjectGitStatus {
  enabled: boolean
  // The project's configured Base branch exactly as the server reports it, or null when it
  // is not configured — including when the server only reports its own main-branch fallback
  // (base_branch_configured is not true). Never defaulted on the client (0024-D §12).
  baseBranch: string | null
  slots: ProjectGitSlot[]
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
}
