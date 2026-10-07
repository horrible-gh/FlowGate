import { isRecord } from './apiErrors'
import { normalizeGitError } from './gitErrors'

/**
 * flowgate.default.0683 T0004 §3 — who holds a merge target's workspace.
 *
 * A finalize/approval refused with `merge_target_busy` (or `merge_target_owner_mismatch`)
 * carries the blocking attempt in `error.details`. The approval screen used to drop those
 * details and print "another finalize attempt owns this target branch's workspace" even
 * when the holder was an ordinary branch merge waiting for its review. This reads the
 * details from any of the shapes the approve route returns: an axios error, a response
 * body (`{ error }`), or the ride-along `git` block (`{ ok: false, error }`).
 */
export type MergeTargetBlockerCode = 'merge_target_busy' | 'merge_target_owner_mismatch'

export interface MergeTargetBlocker {
  readonly code: MergeTargetBlockerCode
  readonly targetBranch: string | null
  readonly mergeId: number | null
  /** `branch_merge` | `group` (server `db_git.OWNER_*`); null when the server did not say. */
  readonly ownerType: string | null
  readonly groupId: string | null
  readonly sourceBranch: string | null
  readonly startedAt: string | null
  /** The screen state of the blocker (`resolved_pending_review`, `conflict`, …). */
  readonly state: string | null
  readonly reviewState: string | null
  readonly projectId: string | null
  /** The blocker's own API base (`/api/v1/projects/{p}/git/merge/{id}` for a branch merge). */
  readonly route: string | null
  readonly reason: string | null
}

const BLOCKER_CODES = new Set<string>(['merge_target_busy', 'merge_target_owner_mismatch'])

function text(value: unknown): string | null {
  return typeof value === 'string' && value.trim() ? value : null
}

function blockerFromSource(input: unknown): MergeTargetBlocker | null {
  const error = normalizeGitError(input)
  if (!error.code || !BLOCKER_CODES.has(error.code)) return null
  const d = error.details
  const mergeId = Number(d.merge_id)
  return Object.freeze({
    code: error.code as MergeTargetBlockerCode,
    targetBranch: text(d.target_branch),
    mergeId: Number.isInteger(mergeId) && mergeId > 0 ? mergeId : null,
    ownerType: text(d.blocking_owner_type),
    groupId: text(d.blocking_group_id),
    sourceBranch: text(d.source_branch),
    startedAt: text(d.started_at),
    state: text(d.blocking_state) ?? text(d.review_state) ?? text(d.attempt_state),
    reviewState: text(d.review_state),
    projectId: text(d.project_id),
    route: text(d.route),
    reason: text(d.reason),
  })
}

/** The blocker named by an approve failure, or null when the failure is something else. */
export function mergeTargetBlockerOf(input: unknown): MergeTargetBlocker | null {
  const direct = blockerFromSource(input)
  if (direct) return direct
  // An approve answer carries the same error a second time under `git`.
  const root = isRecord(input) ? input : {}
  const response = isRecord(root.response) ? root.response : {}
  const data = isRecord(response.data) ? response.data : root
  return isRecord(data.git) ? blockerFromSource(data.git) : null
}

/** A branch merge blocker can be opened in place (its review/resolver dialogs). */
export function isOpenableBranchMergeBlocker(blocker: MergeTargetBlocker | null): boolean {
  return !!blocker && blocker.code === 'merge_target_busy'
    && blocker.ownerType === 'branch_merge' && blocker.mergeId != null
}
