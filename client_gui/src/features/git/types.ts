// flowgate.v02.0033 — model of the Designer Git part. The server's snake_case stays inside
// api.ts (and features/project for the read loaders); the session, the cards and whatever a
// Tool Panel Container does with the part's events only ever see these shapes.
//
// Project / Base Git status itself is features/project's ProjectGitStatus (0033-D §4.2: no
// Git-only store); this file adds what the part derives from it and what only it owns.
//
// flowgate.v02.0066 adds the Group worktree models (Worktree* / ManualMerge* / MergeResult* /
// RefsFetch*) at the end. They belong to the Group worktree of the current Flow, never to the
// Base checkout card (GitBase*): 0066 D#2 §2-1.
import type { GroupScopeFloor } from '@/features/project'

/** Tool Panel tab badge (0033-D §3.8). One at a time: danger > progress > count. */
export interface GitBadge {
  tone: 'danger' | 'progress' | 'count'
  text: string
}

/** File state letter shown in a Git file row. null = not known (yet). */
export type GitFileLetter = 'C' | 'A' | 'D' | 'M' | '?'

// worktree (flowgate.v02.0066): a Group worktree file, or a file of the target comparison list.
export type GitFileOrigin = 'conflict' | 'dirty' | 'review' | 'worktree'

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
  // group (flowgate.v02.0066): GET /projects/{pid}/git/groups/{gid}/diff?path=[&base=]
  diffSource: 'base' | 'review' | 'group'
  // flowgate.v02.0066, worktree intents only (the dirty / conflict / review ones never carry the
  // key): null = the group work base floor against the worktree file; a name = the merge-base
  // with that target, i.e. the target the comparison list was read for (0066 D#2 §5-2).
  compareBase?: string | null
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

/** `provisioning` = only `저장소 준비 다시 시도` runs (the settings are already saved). */
export type GitConnectBusy = 'idle' | 'testing' | 'connecting' | 'provisioning'

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

// ── Existing project Git settings contracts (flowgate.v02.0065 T#2) ────────────────────
// The shapes of GET/PUT git/config, POST git/test-connection and GET/POST git/provision as the
// connect window needs them. No token, masked or not, is ever part of a model here.

/** GET git/config — `configured: false` when the project has no Git settings at all. */
export interface GitConfigView {
  configured: boolean
  repoUrl: string
  provider: string
  username: string | null
  baseBranch: string
  enabled: boolean
  // A token is stored (its value never comes into the client model).
  hasSecret: boolean
}

/** What `연결` saves: the window's values with Git turned on; nothing else is sent. */
export interface GitConfigSave {
  repoUrl: string
  provider: GitConnectProvider
  // Blank = null.
  username: string | null
  // null = keep the stored token; a blank input is never sent as "" (that would clear it).
  secret: string | null
  baseBranch: string
}

/** POST git/test-connection. `failureCode` is the server's classification only (its text is dropped). */
export interface GitConnectionTest {
  reachable: boolean | null
  authenticated: boolean | null
  failureCode: string | null
  remoteDefaultBranch: string | null
}

/** GET git/provision — the Base checkout state of the project. */
export interface GitProvisionView {
  configured: boolean
  enabled: boolean
  baseCheckoutExists: boolean
  lastAttemptResult: string | null
}

/** POST git/provision. A failed preparation is an HTTP 200 with `status: 'failed'`. */
export interface GitProvisionResult {
  // ok | failed | skipped
  status: string
  mode: string | null
  // The other Git work held the lock (`reason: git_busy`). Other reasons are server text, dropped.
  busy: boolean
  provision: GitProvisionView | null
}

// ── Group worktree (flowgate.v02.0066) ─────────────────────────────────────────────────
// The Group worktree endpoints of 0066 D#1 E1–E12 as the Git tab reads them (0066 D#2 §5-1).
// No server `message` and no diagnostic text is ever part of these models; an error keeps its
// code and only the detail values the screen reads (WorktreeErrorDetails).

/** The detail values of a Group worktree error the screen may read (0066 D#2 §5-1). */
export interface WorktreeErrorDetails {
  reason: string | null
  jobId: string | null
  mergeId: number | null
  latestMergeId: number | null
  sessionKind: string | null
  targetFastForwarded: boolean | null
  pushVia: string | null
  // Blocking total of an uncommitted / protected refusal, when the server says.
  count: number | null
  // Per-class counts (e.g. hidden / managed / excluded_new), numbers only, server keys.
  counts: Record<string, number>
  protectedUncommitted: number | null
}

/** A server error carried inside an answer body (floor error, preview error, merge block). */
export interface WorktreeBodyError {
  code: string
  details: WorktreeErrorDetails
}

/** A thrown Group worktree request error, read through `worktreeErrorOf`. */
export interface WorktreeRequestFailure {
  code: string | null
  httpStatus: number | null
  network: boolean
  details: WorktreeErrorDetails
}

/** Index letter of a worktree file; null = nothing staged. */
export type WorktreeStageLetter = 'A' | 'M' | 'D' | 'T' | 'U'
/** Working tree letter of a worktree file; null = no unstaged change. */
export type WorktreeWorkLetter = 'M' | 'D' | 'T'

/** One listed (visible) worktree file. Hidden paths are never listed — only counted. */
export interface WorktreeFile {
  path: string
  // true = an existing path (in HEAD), false = a new one.
  inHead: boolean
  staged: WorktreeStageLetter | null
  unstaged: WorktreeWorkLetter | null
  untracked: boolean
  // Operation class (normal / excluded / managed / unverified); an unknown value is kept as is.
  fileClass: string | null
  // null / managed / excluded_new / ownership_unavailable (unknown values kept).
  protectedReason: string | null
  // null / type_change / unmerged (unknown values kept).
  unsupported: string | null
  // What the row may do — only these decide an operation, true only when the server says so.
  canStage: boolean
  canUnstage: boolean
  canRevert: boolean
  canRemove: boolean
  insertions: number | null
  deletions: number | null
}

/** E1 `counts`, exactly the server values (never filled with 0, never trimmed). */
export interface WorktreeCounts {
  changed: number | null
  staged: number | null
  unstaged: number | null
  untracked: number | null
  excludedNewUntracked: number | null
  hidden: number | null
  managedDirty: number | null
  unsupported: number | null
}

export interface WorktreeGitState {
  // none / awaiting_choice / waiting / merging / conflict / merged / pushed.
  status: string | null
  // Information only (never a reason to disable anything).
  rootWfDone: boolean
  finalApprovalBound: boolean
}

/** The group scope floor of E1, with the floor 409 the server put in the body instead. */
export interface WorktreeFloor extends GroupScopeFloor {
  error: WorktreeBodyError | null
}

/** Commits since the floor, for display only (never the merge need). */
export interface WorktreeCommitted {
  commitCount: number | null
  changedFileCount: number | null
}

export interface WorktreeOwnership {
  available: boolean
  // null / tr_history_recovery_required / ownership_invariant / ownership_read_failed.
  reason: string | null
}

/** A reason that blocks write operations now, with the operations it blocks. */
export interface WorktreeBlocker {
  code: string
  operations: string[]
}

/** GET /groups/{gid}/git/worktree/status (E1), also the `status` of every E2–E6 answer. */
export interface WorktreeStatus {
  groupId: string
  branch: string | null
  head: string | null
  // false = no slot / workspace: no files, no tokens, no committed range.
  worktreeAvailable: boolean
  worktreeToken: string | null
  indexToken: string | null
  gitState: WorktreeGitState
  floor: WorktreeFloor
  committed: WorktreeCommitted | null
  files: WorktreeFile[]
  counts: WorktreeCounts
  ownership: WorktreeOwnership
  blockers: WorktreeBlocker[]
}

/** E2 / E3 target: some paths, or every entry of the group (`all`). */
export type WorktreeSelection = { all: true } | { paths: string[] }

/** What a full Stage skipped, per class. */
export interface WorktreeSkipped {
  managed: number
  hidden: number
  excludedNew: number
  unsupported: number
}

/** The refreshed E1 status every E2–E6 answer carries; null + `statusError` when it failed. */
export interface WorktreeStatusAfter {
  status: WorktreeStatus | null
  statusError: string | null
}

/** POST worktree/stage (E2) / worktree/unstage (E3). */
export interface WorktreeStageResult extends WorktreeStatusAfter {
  // Applied visible paths only.
  applied: string[]
  // Hidden entries a full unstage released (0 otherwise).
  appliedHiddenCount: number
  skipped: WorktreeSkipped
}

/** POST worktree/commit (E4). */
export interface WorktreeCommitResult extends WorktreeStatusAfter {
  commitSha: string | null
  commit: string | null
  // The normalized subject the server really used.
  subject: string | null
  committedPaths: string[]
  committedCount: number
}

export interface WorktreeFileResult {
  path: string
  // reverted / removed / not_changed
  result: string
}

/** POST worktree/revert (E5) / worktree/remove (E6). */
export interface WorktreeFilesResult extends WorktreeStatusAfter {
  results: WorktreeFileResult[]
}

/** POST worktree/commit-message/suggest (E7): a draft only, nothing is committed. */
export interface WorktreeCommitSuggestion {
  suggested: string
  providerId: string | null
  stagedCount: number | null
  // The draft saw only part of the staged changes.
  inputTruncated: boolean
  // The snapshot the draft was made from.
  head: string | null
  indexToken: string | null
}

// ── Manual merge (E8 / E9) ─────────────────────────────────────────────────────────────

/** One failed admission condition of E8, in the server's order. */
export interface ManualMergeBlock {
  code: string
  reason: string | null
  details: WorktreeErrorDetails
}

/** One merge target candidate. Only `eligible` ones can be chosen. */
export interface ManualMergeTarget {
  name: string
  // base / local (unknown values kept).
  kind: string | null
  eligible: boolean
  // null / remote_only / internal_slot / workspace_busy / owner_mismatch.
  blockedReason: string | null
  isWorkBase: boolean
}

export interface ManualMergePreview {
  targetBranch: string | null
  workBaseRef: string | null
  projectBaseBranch: string | null
  isWorkBase: boolean
  isProjectBase: boolean
  incomingWorkBaseCommits: number | null
  // e.g. target_differs_from_work_base, unmerge_unsupported_for_non_base_target.
  warnings: string[]
  // The preview could not be made (the rest is then empty).
  error: WorktreeBodyError | null
}

export interface ManualMergeUncommitted {
  // Uncommitted entries that block a merge.
  blocking: number | null
  counts: Record<string, number>
  // Hidden + managed entries the Git tab cannot handle.
  protectedUncommitted: number | null
  // ownership_unavailable when the excluded-new exemption could not be applied.
  reason: string | null
}

/** Graph run information (information only, except `blocker`, which E9 also refuses on). */
export interface ManualMergeGraph {
  runPresent: boolean | null
  blocker: string | null
  gateOpen: boolean | null
}

/** Approval information — never a reason to disable a merge (0066 D#1 Q1). */
export interface ManualMergeApproval {
  acDocId: string | null
  status: string | null
  finalApprovalBound: boolean
}

/** GET /groups/{gid}/git/manual-merge[?target=] (E8). */
export interface ManualMergeView {
  canMerge: boolean
  blocked: ManualMergeBlock[]
  // wf_done / no_wf_done, information only.
  admissionBasis: string | null
  approval: ManualMergeApproval
  // The group work base (work_base_ref).
  defaultTarget: string | null
  targets: ManualMergeTarget[]
  preview: ManualMergePreview | null
  // Commits since the floor, display only; null + `floorError` when the floor is unusable.
  commitCount: number | null
  floorError: WorktreeBodyError | null
  // Merge need against the requested (or default) target.
  aheadOfTarget: number | null
  mergeNeeded: boolean
  // A merge already in the local target but not on its remote is what a merge would publish.
  recovery: boolean
  uncommitted: ManualMergeUncommitted | null
  graph: ManualMergeGraph | null
}

/** POST /groups/{gid}/git/manual-merge (E9). */
export interface ManualMergeResult {
  // merged / conflict
  status: string
  groupStatus: string | null
  // Always true for a manual merge: the group and its workspace stay.
  worktreeKept: boolean
  // merge / merge_only
  action: string | null
  targetBranch: string | null
  // Short / full merge commit.
  mergeCommit: string | null
  mergeCommitSha: string | null
  pushed: boolean
  // The merge ended through the unpushed-merge recovery path.
  recovered: boolean
  mergeId: number | null
  // Conflict files, hidden ones included (their paths are never in the model).
  conflictFileCount: number
  // New tool artifacts left uncommitted in the workspace.
  excludedArtifactCount: number | null
  jobId: string | null
}

// ── Merge results and follow-up push (E10 / E11) ───────────────────────────────────────

/** The target of a recorded merge (the server's public target fields the screen uses). */
export interface MergeResultTarget {
  targetBranch: string
  // Project Base by the current Base setting.
  isProjectBase: boolean
}

/** The remote state of one recorded merge, from local remote-tracking refs. */
export interface MergeResultRemote {
  measured: boolean
  remoteBranchMissing: boolean | null
  localAheadCount: number | null
  containsMergeCommit: boolean | null
}

/** The latest completed manual merge of one target. */
export interface MergeResultRow {
  mergeId: number
  target: MergeResultTarget
  // Full SHA, sent back with a follow-up push to confirm the record.
  mergeCommit: string | null
  pushed: boolean
  recovered: boolean
  completedAt: string | null
  remote: MergeResultRemote
  // Information only — E11 decides under the locks.
  pushAvailable: boolean
  // null / already_published / target_moved / unmeasured / group_blocked.
  pushBlockedReason: string | null
}

/** GET /groups/{gid}/git/merge-result (E10). */
export interface MergeResultsView {
  groupStatus: string | null
  // Newest first, one row per target.
  results: MergeResultRow[]
  pushCandidates: number
  groupBlocker: string | null
}

/** POST /groups/{gid}/git/merge-result/push (E11). */
export interface MergeResultPushResult {
  pushedRef: string | null
  pushedHead: string | null
  // The target the server settled from the merge record.
  targetBranch: string | null
  isProjectBase: boolean
  mergeId: number | null
  mergeCommit: string | null
  // The remote already had it: nothing was pushed.
  alreadyPublished: boolean
  jobId: string | null
  phase: string | null
}

// ── Fetch-only refresh (E12) ───────────────────────────────────────────────────────────

/** A local branch against its remote-tracking ref. null = not measured. */
export interface RefsFetchBranchState {
  branch: string | null
  remoteBranchMissing: boolean | null
  aheadCount: number | null
  behindCount: number | null
}

export interface RefsFetchGroupResult {
  mergeId: number
  target: MergeResultTarget
  remote: MergeResultRemote
  pushAvailable: boolean
  pushBlockedReason: string | null
}

/** The group part of a fetch-only refresh (sent when a group id went out). */
export interface RefsFetchGroup {
  groupId: string
  workBaseRef: string | null
  workBase: RefsFetchBranchState
  results: RefsFetchGroupResult[]
  pushCandidates: number
  groupBlocker: string | null
}

/** POST /projects/{pid}/git/fetch-refs (E12): remote-tracking refs only, no local change. */
export interface RefsFetchResult {
  fetched: boolean
  baseBranch: string | null
  aheadCount: number | null
  behindCount: number | null
  group: RefsFetchGroup | null
}
