// flowgate.v02.0033 — the one data boundary of the Designer Git part (0033-D §2.2). The
// session talks only to a GitSource. The default one reads through the features/project
// loaders (git/status, AI activity, Base / review file diff — 0033-D §2.3: no Git-only store,
// one git/status reader in v0.2) and acts through features/git/api.ts. Tests hand in their own
// to replay any server state, including the S1 / S2 stage-2 fields.
//
// flowgate.v02.0107 adds the display read of the Flow's work base (GitWorktreeSource.loadWorkBase,
// D §3.1): the HTTP source reads it through the project store's side-effect-free display boundary,
// never through the 0104 settings loader (loadFlowWorkBase), and a test source hands in the answer.
//
// flowgate.v02.0066 adds the Group worktree boundary (GitWorktreeSource) as the optional
// `worktree` member: the worktree area appears only for a source that has one, so a test
// source without it keeps the 0033 / 0065 behaviour unchanged (0066 D#2 §2-3, §3-1).
import { useProjectStore } from '@/features/project'
import type {
  BaseFileDiff,
  FlowAiActivity,
  FlowChangeSet,
  FlowFileDiff,
  FlowWorkBase,
  MergeReviewFileDiff,
  ProjectGitStatus,
} from '@/features/project'
import * as api from './api'
import type {
  GitApproveResult,
  GitBaseCommitResult,
  GitBaseRemoveResult,
  GitBaseRevertAllResult,
  GitBaseRevertResult,
  GitConfigSave,
  GitConfigView,
  GitConflictSession,
  GitConnectionTest,
  GitFinalizeAction,
  GitFinalizeResult,
  GitProviders,
  GitProvisionResult,
  GitProvisionView,
  GitRejectResult,
  GitReview,
  ManualMergeResult,
  ManualMergeView,
  MergeResultPushResult,
  MergeResultsView,
  RefsFetchResult,
  WorktreeCommitResult,
  WorktreeCommitSuggestion,
  WorktreeFilesResult,
  WorktreeSelection,
  WorktreeStageResult,
  WorktreeStatus,
} from './types'

/**
 * The Group worktree of the current Flow (flowgate.v02.0066, 0066 D#2 §5-1): its own reads and
 * actions, apart from the Base checkout ones of GitSource (baseCommit / baseRevert / …).
 */
export interface GitWorktreeSource {
  /** E1 — files split into Stage / working changes, tokens, floor, ownership and blockers. */
  loadStatus(groupId: string): Promise<WorktreeStatus>
  /** E2 / E3 — `worktreeToken` is the E1 token the screen showed (null = no check). */
  stage(groupId: string, selection: WorktreeSelection, worktreeToken: string | null): Promise<WorktreeStageResult>
  unstage(groupId: string, selection: WorktreeSelection, worktreeToken: string | null): Promise<WorktreeStageResult>
  /** E4 — commit what is staged; `indexToken` is the E1 index token the confirmation showed. */
  commit(groupId: string, message: string, indexToken: string | null): Promise<WorktreeCommitResult>
  /** E5 existing paths / E6 new paths. */
  revert(groupId: string, paths: string[], worktreeToken: string | null): Promise<WorktreeFilesResult>
  remove(groupId: string, paths: string[], worktreeToken: string | null): Promise<WorktreeFilesResult>
  /** E7 — an AI draft only; nothing is committed. */
  suggestCommitMessage(groupId: string, providerId: string): Promise<WorktreeCommitSuggestion>
  /** E8 — `target` null = the group's default target (its work base). */
  loadManualMerge(groupId: string, target: string | null): Promise<ManualMergeView>
  /** E9 — merge (and push the target when `push`). */
  manualMerge(groupId: string, target: string | null, push: boolean): Promise<ManualMergeResult>
  /** E10 — latest completed manual merge per target. */
  loadMergeResults(groupId: string): Promise<MergeResultsView>
  /** E11 — push the target of one recorded merge (no branch name goes out). */
  pushMergeResult(groupId: string, mergeId: number, mergeCommit: string): Promise<MergeResultPushResult>
  /** E12 — fetch-only refresh of the remote-tracking refs. */
  fetchRefs(projectId: string, groupId: string | null): Promise<RefsFetchResult>
  /** The existing /changes?base= list against one target (the `Diff` panel). */
  loadTargetChanges(projectId: string, groupId: string, target: string): Promise<FlowChangeSet>
  /**
   * flowgate.v02.0067 T#4 — the existing group file diff (GET .../groups/{gid}/diff?path=[&base=]) the
   * Git modeless `Diff` tab shows; `compareBase` null = the work base floor (no `base` sent).
   */
  loadFileDiff(projectId: string, groupId: string, path: string, compareBase: string | null): Promise<FlowFileDiff>
  /**
   * flowgate.v02.0107 — the existing GET /groups/{gid}/work-base for display only (D §3.1): its
   * `effectiveWorkBaseRef` is the Flow base the Git header and status tab show. Never an argument
   * of E1–E12; it changes no shared project state.
   */
  loadWorkBase(groupId: string): Promise<FlowWorkBase>
}

export interface GitSource {
  loadStatus(projectId: string): Promise<ProjectGitStatus | null>
  loadAiActivity(groupId: string): Promise<FlowAiActivity>
  loadBaseFileDiff(projectId: string, path: string): Promise<BaseFileDiff>
  loadReviewFileDiff(groupId: string, mergeId: number, path: string): Promise<MergeReviewFileDiff>

  fetchConflicts(groupId: string, mergeId: number): Promise<GitConflictSession>
  startConflictAi(groupId: string, mergeId: number): Promise<string | null>
  cancelAiRun(runId: string): Promise<string | null>
  abortMerge(groupId: string, mergeId: number): Promise<void>

  fetchReview(groupId: string, mergeId: number): Promise<GitReview>
  approveReview(groupId: string, mergeId: number, attemptId: string, fingerprint: string): Promise<GitApproveResult>
  rejectReview(groupId: string, mergeId: number, reason: string, providerId: string): Promise<GitRejectResult>
  fetchProviders(projectId: string): Promise<GitProviders>

  finalize(groupId: string, action: GitFinalizeAction, targetBranch: string | null): Promise<GitFinalizeResult>
  push(projectId: string, branch: string | null): Promise<GitFinalizeResult>

  baseCommit(projectId: string, message: string, paths: string[]): Promise<GitBaseCommitResult>
  baseRevert(projectId: string, files: string[]): Promise<GitBaseRevertResult>
  baseRemove(projectId: string, files: string[]): Promise<GitBaseRemoveResult>

  /**
   * S2 "tracked + every new file" commit with the new-file total the screen showed
   * (0033-D §4.6). The server contract is not fixed yet, so the HTTP source leaves this out
   * and the part stays on the stage-1 rule until a source provides it.
   */
  baseCommitAll?(projectId: string, message: string, expectedUntrackedTotal: number): Promise<GitBaseCommitResult>
  /**
   * S2 "revert everything" as one request: revert the named tracked files and delete every
   * new file. The server checks the expected new-file total before touching anything and, on a
   * mismatch, refuses with nothing changed (0033-D §3.5, §4.6) — tracked files included, which
   * a revert-then-delete pair of requests could not promise. Absent until the contract exists.
   */
  baseRevertAll?(projectId: string, trackedFiles: string[], expectedUntrackedTotal: number): Promise<GitBaseRevertAllResult>

  // flowgate.v02.0065 — `Git 연결`: the existing project Git settings contracts (api.ts).
  fetchGitConfig(projectId: string): Promise<GitConfigView>
  testGitConnection(projectId: string, input: GitConfigSave): Promise<GitConnectionTest>
  saveGitConfig(projectId: string, input: GitConfigSave): Promise<GitConfigView>
  provisionGit(projectId: string): Promise<GitProvisionResult>
  fetchGitProvision(projectId: string): Promise<GitProvisionView | null>

  // flowgate.v02.0066 — the Group worktree. Absent = no worktree area (and no worktree request).
  worktree?: GitWorktreeSource
}

export function createHttpGitSource(): GitSource {
  const projectStore = useProjectStore()
  return {
    loadStatus: (projectId) => projectStore.loadProjectGitStatus(projectId),
    loadAiActivity: (groupId) => projectStore.loadFlowAiActivity(groupId),
    loadBaseFileDiff: (projectId, path) => projectStore.loadBaseFileDiff(projectId, path),
    loadReviewFileDiff: (groupId, mergeId, path) => projectStore.loadMergeReviewFileDiff(groupId, mergeId, path),
    fetchConflicts: api.fetchConflicts,
    startConflictAi: api.startConflictAi,
    cancelAiRun: api.cancelAiRun,
    abortMerge: api.abortMerge,
    fetchReview: api.fetchReview,
    approveReview: api.approveReview,
    rejectReview: api.rejectReview,
    fetchProviders: api.fetchProviders,
    finalize: api.finalizeGroup,
    push: api.pushBranch,
    baseCommit: api.baseCommit,
    baseRevert: api.baseRevert,
    baseRemove: api.baseRemove,
    fetchGitConfig: api.fetchGitConfig,
    testGitConnection: api.testGitConnection,
    saveGitConfig: api.saveGitConfig,
    provisionGit: api.provisionGit,
    fetchGitProvision: api.fetchGitProvision,
    worktree: {
      loadStatus: api.fetchWorktreeStatus,
      stage: api.stageWorktree,
      unstage: api.unstageWorktree,
      commit: api.commitWorktree,
      revert: api.revertWorktree,
      remove: api.removeWorktreeFiles,
      suggestCommitMessage: api.suggestWorktreeCommitMessage,
      loadManualMerge: api.fetchManualMerge,
      manualMerge: api.requestManualMerge,
      loadMergeResults: api.fetchMergeResults,
      pushMergeResult: api.pushMergeResult,
      fetchRefs: api.fetchRefs,
      loadTargetChanges: (projectId, groupId, target) => projectStore.loadFlowChanges(projectId, groupId, { base: target }),
      loadFileDiff: (projectId, groupId, path, compareBase) =>
        projectStore.loadFlowFileDiff(projectId, groupId, path, compareBase ? { base: compareBase } : {}),
      loadWorkBase: (groupId) => projectStore.readFlowWorkBaseForDisplay(groupId),
    },
  }
}
