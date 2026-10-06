// flowgate.v02.0033 — the one data boundary of the Designer Git part (0033-D §2.2). The
// session talks only to a GitSource. The default one reads through the features/project
// loaders (git/status, AI activity, Base / review file diff — 0033-D §2.3: no Git-only store,
// one git/status reader in v0.2) and acts through features/git/api.ts. Tests hand in their own
// to replay any server state, including the S1 / S2 stage-2 fields.
import { useProjectStore } from '@/features/project'
import type { BaseFileDiff, FlowAiActivity, MergeReviewFileDiff, ProjectGitStatus } from '@/features/project'
import * as api from './api'
import type {
  GitApproveResult,
  GitBaseCommitResult,
  GitBaseRemoveResult,
  GitBaseRevertAllResult,
  GitBaseRevertResult,
  GitConflictSession,
  GitFinalizeAction,
  GitFinalizeResult,
  GitProviders,
  GitRejectResult,
  GitReview,
} from './types'

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
  }
}
