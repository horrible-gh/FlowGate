import type { GitWorktreeSource } from './gitSource'
import type { WorktreeSelection, WorktreeStatus } from './types'
import { worktreeErrorOf } from './api'

export interface GitExecutionRequest {
  scope: GitExecutionScope
  target: string | null
  message: string
  provider_id: string | null
  paths?: string[]
  all?: boolean
  request_id: string
  resume_from?: string
  commit_remaining?: boolean
}

export type GitExecutionScope = 'commit' | 'merge' | 'push'
export type GitExecutionStep = 'commit' | 'update' | 'merge' | 'push'
export interface GitExecution {
  jobId?: string
  jobStatus?: string
  mergeId?: number | null
  childJobId?: string | null
  scope: GitExecutionScope
  target: string | null
  steps: GitExecutionStep[]
  cursor: number
  completed: GitExecutionStep[]
  status: 'running' | 'stopped' | 'remaining' | 'complete'
  error: string | null
  // The server's detail reason of the stop, when it gave one (shown next to the code).
  detail?: string | null
  // A step passed over without stopping the run (only 받아오기), with the server's code.
  skipped?: Partial<Record<GitExecutionStep, string>>
}
/** flowgate.v02test.0068: what the Git panel tells the Designer's Flow tab about the shown Flow's run (`git-activity`). */
export interface GitTabActivity {
  readonly state: 'running' | 'done' | 'skipped' | 'failed'
  readonly step: GitExecutionStep | null
  readonly code: string | null
}
export function executionSteps(scope: GitExecutionScope): GitExecutionStep[] {
  return scope === 'commit' ? ['commit'] : scope === 'merge'
    ? ['commit', 'update', 'merge'] : ['commit', 'update', 'merge', 'push']
}
export function newExecution(scope: GitExecutionScope, target: string | null): GitExecution {
  return { scope, target, steps: executionSteps(scope), cursor: 0, completed: [], status: 'running', error: null, detail: null, skipped: {} }
}
// 받아오기 is a convenience before the merge, not a precondition of it: a refusal caused by the
// server's base checkout, its locks or the Flow's start-point record passes over the step and the
// merge runs (it checks the target itself). Conflicts and the Flow's own files still stop the run.
export const UPDATE_SKIP_CODES: readonly string[] = [
  'git_error', 'base_dirty', 'base_diverged', 'git_busy', 'merge_conflict_open', 'group_lease_active',
  'run_already_active', 'branch_merge_claim_active', 'update_unavailable',
  'group_work_base_unverified', 'group_work_base_diverged', 'group_work_base_source_rewritten',
  'group_work_base_record_failed',
]
class Stopped extends Error {}
export interface ExecutionInput {
  projectId: string
  groupId: string
  message: string
  providerId: string | null
  selection: WorktreeSelection
  current(): boolean
}

/** Reads/mutations are lazy: creating a plan never calls Git or the network.
 * A plan pins its target and resumes at the interrupted step. Every await is fenced
 * before another mutation, including a late response after changing Flow.
 */
export async function executeGit(plan: GitExecution, api: GitWorktreeSource, input: ExecutionInput): Promise<void> {
  const guard = () => { if (!input.current()) throw new Stopped('context_changed') }
  const request = async <T>(send: () => Promise<T>): Promise<T> => {
    guard(); const result = await send(); guard(); return result
  }
  const status = () => request(() => api.loadStatus(input.groupId))
  const after = async (result: { status: WorktreeStatus | null }) => result.status ?? await status()
  const retryPush = plan.status === 'stopped' && plan.steps[plan.cursor] === 'push' && plan.error === 'push_rejected'
  plan.status = 'running'; plan.error = null; plan.detail = null
  try {
    while (plan.cursor < plan.steps.length) {
      guard()
      const step = plan.steps[plan.cursor]
      if (step === 'commit') {
        let state = await status()
        if (!state.worktreeAvailable) throw new Stopped('git_unavailable')
        if (state.counts.changed === null) throw new Stopped('status_unavailable')
        if (state.counts.changed > 0) {
          if ('paths' in input.selection) {
            if (!input.selection.paths.length) throw new Stopped('nothing_selected')
            const selected = new Set(input.selection.paths)
            const excluded = state.files.filter(f => f.staged && !selected.has(f.path)).map(f => f.path)
            if (excluded.length) state = await after(await request(() => api.unstage(input.groupId, { paths: excluded }, state.worktreeToken)))
          }
          state = await after(await request(() => api.stage(input.groupId, input.selection, state.worktreeToken)))
          if (!state.indexToken || !state.counts.staged) throw new Stopped('nothing_staged')
          let message = input.message.trim()
          if (!message) {
            if (!input.providerId) throw new Stopped('commit_message_required')
            const suggestion = await request(() => api.suggestCommitMessage(input.groupId, input.providerId!))
            if (suggestion.indexToken !== state.indexToken) throw new Stopped('worktree_changed')
            message = suggestion.suggested.trim()
          }
          if (!message) throw new Stopped('commit_message_required')
          const committed = await request(() => api.commit(input.groupId, message, state.indexToken))
          if (!committed.commitSha && !committed.commit) throw new Stopped('commit_unconfirmed')
        }
      } else if (step === 'update') {
        const state = await status()
        if (state.counts.changed === null || state.counts.changed > 0) throw new Stopped('uncommitted_changes')
        delete plan.skipped?.update
        let code: string | null = null
        if (!api.updateFromBase) code = 'update_unavailable'
        else {
          try {
            const result = await request(() => api.updateFromBase!(input.groupId))
            if (result.status === 'conflict') throw new Stopped('merge_conflict')
            if (!['updated', 'no_change'].includes(result.status)) throw new Stopped('update_unconfirmed')
          } catch (error) {
            if (error instanceof Stopped) throw error
            const failure = worktreeErrorOf(error)
            if (!failure.code || !UPDATE_SKIP_CODES.includes(failure.code)) throw error
            code = failure.code
          }
        }
        if (code) plan.skipped = { ...plan.skipped, update: code }
      } else if (step === 'merge') {
        const view = await request(() => api.loadManualMerge(input.groupId, plan.target))
        if (!plan.target) plan.target = view.defaultTarget
        if (!plan.target || view.aheadOfTarget === null) throw new Stopped('status_unavailable')
        if (view.aheadOfTarget > 0) {
          if (!view.canMerge) throw new Stopped(view.blocked[0]?.code ?? 'merge_blocked')
          const result = await request(() => api.manualMerge(input.groupId, plan.target, false))
          if (result.status === 'conflict') throw new Stopped('merge_conflict')
          if (result.status !== 'merged') throw new Stopped(result.jobId ? 'queued' : 'merge_unconfirmed')
        }
      } else {
        if (retryPush) {
          if (!plan.target || !api.syncRemote) throw new Stopped('remote_sync_required')
          const sync = await request(() => api.syncRemote!(input.groupId, plan.target!))
          if (sync.status === 'conflict') throw new Stopped('merge_conflict')
          if (!['updated', 'no_change'].includes(sync.status)) throw new Stopped('remote_sync_required')
        }
        await request(() => api.fetchRefs(input.projectId, input.groupId))
        const results = await request(() => api.loadMergeResults(input.groupId))
        const row = results.results.find(r => r.target.targetBranch === plan.target)
        if (!row || !row.remote.measured) throw new Stopped('status_unavailable')
        if (row.remote.containsMergeCommit !== true || (row.remote.localAheadCount ?? 0) > 0) {
          if (!row.pushAvailable || !row.mergeCommit) throw new Stopped(row.pushBlockedReason ?? 'push_blocked')
          const result = await request(() => api.pushMergeResult(input.groupId, row.mergeId, row.mergeCommit!))
          if (!result.pushedHead && !result.alreadyPublished) throw new Stopped(result.jobId ? 'queued' : 'push_unconfirmed')
        }
      }
      plan.completed.push(step)
      plan.cursor += 1
    }
    // A successful HTTP call alone is never overall success. Check the selected scope.
    const final = await status()
    let remains = final.counts.changed !== 0 || (final.counts.staged ?? 0) > 0 || (final.counts.hidden ?? 0) > 0 || (final.counts.managedDirty ?? 0) > 0
    if (plan.scope !== 'commit') {
      const view = await request(() => api.loadManualMerge(input.groupId, plan.target))
      // A passed-over 받아오기 leaves the Flow behind its base by design; that alone is not left work.
      remains ||= view.aheadOfTarget !== 0 || (!plan.skipped?.update && view.workBaseBehind !== 0)
    }
    if (plan.scope === 'push') {
      await request(() => api.fetchRefs(input.projectId, input.groupId))
      const results = await request(() => api.loadMergeResults(input.groupId))
      const row = results.results.find(r => r.target.targetBranch === plan.target)
      remains ||= !row?.remote.measured || row.remote.containsMergeCommit !== true || row.remote.localAheadCount !== 0
    }
    plan.status = remains ? 'remaining' : 'complete'
  } catch (error) {
    if (!input.current()) return
    plan.status = 'stopped'
    if (error instanceof Stopped) { plan.error = error.message; return }
    const failure = worktreeErrorOf(error)
    plan.error = failure.network ? 'network' : failure.httpStatus === 403 && !failure.code ? 'forbidden' : failure.code ?? 'request_failed'
    plan.detail = failure.details.reason
  }
}
