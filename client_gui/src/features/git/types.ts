// flowgate.v02.0033 — model of the Designer Git part. The server's snake_case stays inside
// api.ts (and features/project for the read loaders); the session, the cards and whatever a
// Tool Panel Container does with the part's events only ever see these shapes.
//
// Project / Base Git status itself is features/project's ProjectGitStatus (0033-D §4.2: no
// Git-only store); this file adds what the part derives from it and what only it owns.

/** Tool Panel tab badge (0033-D §3.8). One at a time: danger > progress > count. */
export interface GitBadge {
  tone: 'danger' | 'progress' | 'count'
  text: string
}

/** File state letter shown in a Git file row. null = not known (yet). */
export type GitFileLetter = 'C' | 'A' | 'D' | 'M' | '?'

export type GitFileOrigin = 'conflict' | 'dirty' | 'review'

/** `open-diff` intent (0033-D §4.7). The Center diff tab is a later group's. */
export interface GitDiffIntent {
  projectId: string
  // Conflict / AI / review files belong to that group; an uncommitted Base file to none.
  groupId: string | null
  // Review files only: the frozen candidate the diff must be read from.
  mergeId: number | null
  // Base checkout relative.
  path: string
  status: GitFileLetter | null
  origin: GitFileOrigin
  // base: GET /projects/{pid}/git/diff?path= · review: GET .../merge/{merge_id}/review-diff?path=
  diffSource: 'base' | 'review'
}

/** `open-confirm-settings` intent (0033-D §4.8). */
export interface GitConfirmSettingsIntent {
  projectId: string
  groupId: string
  acDocId: string | null
}

export type GitWindowKind = 'conflict' | 'push-error'

/** One Git modeless window as the session owns it and `windows-change` reports it (§6.4). */
export interface GitWindowDescriptor {
  id: string
  kind: GitWindowKind
  title: string
  minimized: boolean
  groupId: string | null
  mergeId: number | null
}

// ── Server reads (normalized) ──────────────────────────────────────────────────────────

export interface GitConflictFile {
  path: string
  // Conflict markers still in the file, counted by the server.
  conflictCount: number
}

/** GET /groups/{gid}/git/merge/{merge_id}/conflicts. */
export interface GitConflictSession {
  mergeId: number
  // The group's work branch.
  branch: string | null
  // The project Base as the conflict session reports it.
  baseBranch: string | null
  // The branch the merge lands on, when the server says.
  targetBranch: string | null
  // A TR revert / reapply session (server `kind` tr_revert / tr_reapply, `tr_conflict` set):
  // git/status sends no review_state for it, so this is what keeps it off the merge actions.
  trConflict: boolean
  files: GitConflictFile[]
}

export interface GitReviewChange {
  path: string
  status: string
}

/** GET /groups/{gid}/git/merge/{merge_id}/review — only what the review card shows. */
export interface GitReview {
  mergeId: number
  reviewState: string | null
  fingerprint: string | null
  changes: GitReviewChange[]
  resolverProvider: string | null
  reconciliationKind: string | null
  canApprove: boolean
  canReject: boolean
}

export interface GitApproveResult {
  status: string
  reviewState: string | null
  reconciliationKind: string | null
  // Number of `errors` of a failed apply; their text is never shown.
  errorCount: number
}

export interface GitRejectResult {
  status: string
  resolverRunId: string | null
}

export interface GitProviders {
  providerIds: string[]
  defaultProviderId: string | null
}

/** The finalize actions a planned (S1) finalize may carry; `wait` is not one. */
export type GitFinalizeAction = 'merge' | 'merge_only' | 'push' | 'commit_push'

export interface GitFinalizeResult {
  status: string
}

export interface GitBaseCommitResult {
  committed: boolean
  remaining: string[]
  remainingUntracked: string[]
}

export interface GitBaseFileResult {
  path: string
  result: string
}

export interface GitBaseRevertResult {
  results: GitBaseFileResult[]
  remaining: string[]
}

export interface GitBaseRemoveResult {
  results: GitBaseFileResult[]
  remainingUntracked: string[]
}

/** S2 one-request "revert everything": tracked reverts and new-file deletes together. */
export interface GitBaseRevertAllResult {
  results: GitBaseFileResult[]
  remaining: string[]
  remainingUntracked: string[]
}

// ── Remote-apply failure record (0033-D §3.6, G1) ──────────────────────────────────────

/** A remote-apply request exactly as the part sent it, kept so `다시 반영` resends it. */
export type GitApplyRequest =
  | { kind: 'finalize'; groupId: string; action: GitFinalizeAction; targetBranch: string | null }
  | { kind: 'push'; projectId: string; branch: string | null }

export interface GitPushFailure {
  request: GitApplyRequest
  // Server error code (e.g. push_rejected). The server sends no diagnostic text (G2).
  code: string
  httpStatus: number | null
  // Client receive time, ISO.
  at: string
  // The branch or group the request targeted.
  target: string
}

// ── Inline feedback ────────────────────────────────────────────────────────────────────

export type GitCardKey = 'header' | 'conflict' | 'review' | 'hold' | 'dirty' | 'push' | 'window'

/** A short line inside one card: `gui.git.notice.<code>`. Cleared by the next action. */
export interface GitNotice {
  code: string
  params?: Record<string, string | number>
}

// ── Git connect window (flowgate.v02.0065) ─────────────────────────────────────────────

/** Provider choices of the connect window — the values the existing Git settings use. */
export type GitConnectProvider = 'generic' | 'github' | 'gitlab' | 'gitea' | 'gitbucket'

/** What the connect window hands up with `test` / `connect`. `secret` is the token typed in
 *  the window; it only ever leaves the window in these two payloads. */
export interface GitConnectForm {
  repoUrl: string
  provider: GitConnectProvider
  username: string
  secret: string
  baseBranch: string
}

/** Values the connect window opens with — never the token. */
export type GitConnectInitial = Omit<GitConnectForm, 'secret'>

export type GitConnectBusy = 'idle' | 'testing' | 'connecting'

/** The line beside `연결 테스트`. `messageKey` is a full i18n key. */
export interface GitConnectTestState {
  ok: boolean
  messageKey: string
  params?: Record<string, string | number>
}

/** The window's message area. `messageKey` is a full i18n key. */
export interface GitConnectMessage {
  tone: 'error' | 'info'
  messageKey: string
  params?: Record<string, string | number>
}
