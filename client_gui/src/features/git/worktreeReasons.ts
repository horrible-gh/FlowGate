// flowgate.v02.0066 — the one disabled reason of a Git tab worktree button (0066 D#2 §3-5). Pure:
// the session hands in what it read, this picks the first rule of the priority table that hits.
// T#6 holds the file and commit columns; T#7 adds the merge / follow-up push / fetch columns.
//
// Reasons come only from server values (E1 blockers / counts / row flags, E8 `blocked[]`, E10
// `group_blocker` / `push_blocked_reason`) and the screen's own state (read-only, 403, an action
// running, a judgment being checked). A waiting approval is never a reason (D#1 Q1).
import type {
  ManualMergeBlock,
  ManualMergeTarget,
  ManualMergeView,
  MergeResultRow,
  MergeResultsView,
  WorktreeStatus,
} from './types'

/**
 * The buttons of the worktree area. `unstageAll` is every full unstage entry (quick button, 🔒 box,
 * link); `merge` is `머지` / `머지 + Push`, `push` the follow-up Push, `fetch` the Fetch icon.
 */
export type WorktreeButton = 'stage' | 'unstage' | 'unstageAll' | 'commit' | 'ai' | 'merge' | 'push' | 'fetch'

/**
 * Reason codes with their own text (gui.git.reason.*). A server blocker code without one reads
 * as "사용할 수 없음({code})".
 */
export const WORKTREE_REASON_CODES: readonly string[] = [
  'read_only',
  'forbidden',
  'worktree_unavailable',
  'session_open',
  'run_already_active',
  'group_lease_active',
  'branch_merge_claim_active',
  'group_frozen_for_approval',
  'tr_history_recovery_required',
  'ownership_unavailable',
  'git_inactive',
  'no_staged',
  'staged_protected',
  'no_message',
  'no_provider',
  // T#7 — merge (rank 13–14: the E8 blocked codes with their own text), follow-up push (rank 15).
  'merge_unread',
  'no_target',
  'merge_unavailable',
  'uncommitted_changes',
  'uncommitted_protected',
  'nothing_to_merge',
  'already_recorded',
  'graph_execution_active',
  'git_busy',
  'merge_conflict_open',
  'approval_retry_pending',
  'invalid_state',
  'merge_target',
  'recovery_required',
  'unmeasured',
  'group_blocked',
  'target_moved',
  'already_published',
  'no_push_candidate',
]

export interface WorktreeReasonInput {
  readOnly: boolean
  forbidden: boolean
  // A worktree action or a 0033 action is running: disabled without a reason.
  busy: boolean
  status: WorktreeStatus | null
  // The 0033 group card is a conflict / AI resolve / review / unsupported session.
  sessionCard: boolean
  // Stage no listed row shows (§3-6(0)) and listed staged rows the server will not commit.
  hiddenStaged: number
  stagedProtectedRows: number
  message: string
  providerId: string | null
  // T#7 — the merge judgment of the chosen target and the follow-up push row (absent: in progress).
  merge?: WorktreeMergeInput
  push?: WorktreePushInput
}

/** The E8 judgment as the merge button sees it (0066 D#2 §3-4(3)). */
export interface WorktreeMergeInput {
  // No judgment for the chosen target yet, or voided and read again (rank 4 "확인 중").
  checking: boolean
  // The last E8 read failed: no judgment, no merge.
  failed: boolean
  target: string | null
  view: ManualMergeView | null
}

/** The E10 answer and the row the follow-up Push would send (0066 D#2 §3-4(5)). */
export interface WorktreePushInput {
  results: MergeResultsView | null
  row: MergeResultRow | null
}

export interface WorktreeReason {
  disabled: boolean
  // null with `disabled` = in progress (no text, a progress mark only).
  code: string | null
  // Values of the reason text (e.g. the uncommitted count); absent when the text has none.
  params?: Record<string, string | number>
}

const OK: WorktreeReason = { disabled: false, code: null }
const BUSY: WorktreeReason = { disabled: true, code: null }
const off = (code: string, params?: Record<string, string | number>): WorktreeReason =>
  params ? { disabled: true, code, params } : { disabled: true, code }

// The E1 blocker operation a button stands for (server worktree_manual OPERATIONS + suggest).
const OPERATION: Record<WorktreeButton, readonly string[]> = {
  stage: ['stage', 'revert', 'remove'],
  unstage: ['unstage'],
  unstageAll: ['unstage'],
  commit: ['commit'],
  ai: ['suggest'],
  // E1 blockers (rank 7) never apply here: the merge reads its E8 judgment, the push its E10.
  merge: [],
  push: [],
  fetch: [],
}

/**
 * The reason of an E8 `blocked[]` entry (rank 14, the server's order). The code picks the text;
 * `uncommitted_changes` carries the blocking count and the protected entries the Git tab cannot
 * handle, `nothing_to_merge` of an already recorded merge points at the follow-up Push.
 */
export function mergeBlockReason(block: ManualMergeBlock, view: ManualMergeView | null): WorktreeReason {
  const d = block.details
  if (block.code === 'uncommitted_changes') {
    const n = view?.uncommitted?.blocking ?? d.count ?? 0
    const protectedCount = view?.uncommitted?.protectedUncommitted ?? d.protectedUncommitted ?? 0
    return off(protectedCount > 0 ? 'uncommitted_protected' : 'uncommitted_changes', { n, protected: protectedCount })
  }
  if (block.code === 'nothing_to_merge') return off(d.reason === 'already_recorded' ? 'already_recorded' : 'nothing_to_merge')
  if (block.code.startsWith('merge_target_')) return off('merge_target', { code: block.code })
  // Another Git session of the group is open: the 0033 card above takes it.
  if (block.code === 'invalid_state' && d.sessionKind) return off('session_open')
  return off(block.code)
}

// Ranks 4, 5, 13 and 14 of the merge column: only a judgment of the chosen target admits a merge.
function mergeReason(input: WorktreeReasonInput): WorktreeReason {
  const m = input.merge
  if (!m || m.checking) return BUSY
  if (input.status && !input.status.worktreeAvailable) return off('worktree_unavailable')
  if (m.failed) return off('merge_unread')
  if (!m.target || !m.view?.targets.some((t) => t.name === m.target && t.eligible)) return off('no_target')
  const first = m.view.blocked[0]
  if (first) return mergeBlockReason(first, m.view)
  if (!m.view.canMerge) return off('merge_unavailable')
  return OK
}

// Rank 15: the group blocker, else the row's own reason; only a push-available row is sent.
function pushReason(input: WorktreeReasonInput): WorktreeReason {
  const p = input.push
  if (!p?.results) return BUSY
  if (p.results.groupBlocker) return off('group_blocked')
  if (!p.row) {
    const reason = p.results.results.map((r) => r.pushBlockedReason).find((r) => r === 'unmeasured' || r === 'group_blocked')
    return off(reason ?? 'no_push_candidate')
  }
  if (!p.row.pushAvailable) return off(p.row.pushBlockedReason ?? 'no_push_candidate')
  return OK
}

/** The first rule of §3-5 that applies to `button`, or enabled. */
export function worktreeReason(button: WorktreeButton, input: WorktreeReasonInput): WorktreeReason {
  // 1–3: read-only, a 403 seen in this session, an action running.
  if (input.readOnly) return off('read_only')
  if (input.forbidden) return off('forbidden')
  if (input.busy) return BUSY
  // Fetch has no other rule; the follow-up push and the merge have their own columns.
  if (button === 'fetch') return OK
  if (button === 'push') return pushReason(input)
  if (button === 'merge') return mergeReason(input)
  const status = input.status
  if (!status) return BUSY
  // 5: no workspace.
  if (!status.worktreeAvailable) return off('worktree_unavailable')
  // 6: a conflict / update session is open (E1 blocker or the 0033 card above).
  if (input.sessionCard || status.blockers.some((b) => b.code === 'session_open')) return off('session_open')
  // 7: the first E1 blocker aimed at this operation.
  const ops = OPERATION[button]
  const blocker = status.blockers.find((b) => b.operations.some((op) => ops.includes(op)))
  if (blocker) return off(blocker.code)
  // 8 (row can_*) is drawn on the rows themselves; a full unstage never reads it.
  if (button === 'commit' || button === 'ai') {
    if (!status.counts.staged) return off('no_staged')
    if (input.hiddenStaged > 0 || input.stagedProtectedRows > 0) return off('staged_protected')
    if (button === 'commit' && !input.message.trim()) return off('no_message')
    if (button === 'ai' && !input.providerId) return off('no_provider')
  }
  return OK
}

export function worktreeReasons(input: WorktreeReasonInput): Record<WorktreeButton, WorktreeReason> {
  return {
    stage: worktreeReason('stage', input),
    unstage: worktreeReason('unstage', input),
    unstageAll: worktreeReason('unstageAll', input),
    commit: worktreeReason('commit', input),
    ai: worktreeReason('ai', input),
    merge: worktreeReason('merge', input),
    push: worktreeReason('push', input),
    fetch: worktreeReason('fetch', input),
  }
}

/** The i18n key and values of a reason's short line; null = no text (enabled or in progress). */
export function reasonMessage(reason: WorktreeReason): { key: string; params: Record<string, string> } | null {
  if (!reason.disabled || !reason.code) return null
  if (WORKTREE_REASON_CODES.includes(reason.code)) {
    const params: Record<string, string> = {}
    for (const [key, value] of Object.entries(reason.params ?? {})) params[key] = String(value)
    return { key: `gui.git.reason.${reason.code}`, params }
  }
  return { key: 'gui.git.reason.unknown', params: { code: reason.code } }
}

// flowgate.v02.0067 T#1 — the Base apply one-word state (WorktreeApplySummary) as text, shared by the
// Base apply header and the `상태` tab of the Git modeless.
const APPLY_SUMMARY_KEYS: Record<string, string> = {
  merging: 'gui.git.apply.summary.merging',
  conflict: 'gui.git.apply.summary.conflict',
  remote_pending: 'gui.git.apply.summary.remote_pending',
  checking: 'gui.git.apply.summary.checking',
  ahead: 'gui.git.apply.summary.ahead',
  recovery: 'gui.git.apply.summary.recovery',
  nothing: 'gui.git.apply.summary.nothing',
  unknown: 'gui.git.apply.summary.unknown',
}

/** The i18n key and values of the Base apply state word; null = nothing to say. */
export function applySummaryMessage(summary: { kind: string; n?: number } | null): { key: string; params: Record<string, number> } | null {
  if (!summary) return null
  const key = APPLY_SUMMARY_KEYS[summary.kind]
  return key ? { key, params: { n: summary.n ?? 0 } } : null
}

// flowgate.v02.0067 T#3 — what the `Base 반영` tab lists and when it shows the follow-up Push (R §5).

/**
 * The merge targets the `머지 대상` choice lists: only the ones E8 admits (`eligible`). A FlowGate
 * internal work branch is never listed — the server marks it `internal_slot` from its slot
 * registry, not from its name — and neither is a remote-only, busy or foreign one. Nothing is
 * deleted: the branches stay, they are just not offered.
 */
export function mergeTargetChoices<T extends Pick<ManualMergeTarget, 'eligible' | 'blockedReason'>>(targets: readonly T[]): T[] {
  return targets.filter((t) => t.eligible && t.blockedReason !== 'internal_slot')
}

/**
 * How the follow-up Push part shows (null = not at all). It is there only while a recorded merge
 * really waits for its remote — the E10 rows a push may send (one / many), or ones that wait
 * behind the group's Git job (`group_blocked`, a disabled Push and its reason). A row already on
 * its remote, a moved target or a remote state that could not be measured shows nothing.
 */
export function followUpPushMode(
  results: Pick<MergeResultsView, 'results'> | null,
  candidates: number,
): 'one' | 'many' | 'blocked' | null {
  if (!results) return null
  if (candidates === 1) return 'one'
  if (candidates > 1) return 'many'
  return results.results.some((r) => r.pushBlockedReason === 'group_blocked') ? 'blocked' : null
}
