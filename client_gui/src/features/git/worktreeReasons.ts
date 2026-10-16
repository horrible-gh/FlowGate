// flowgate.v02.0066 — the one disabled reason of a Git tab worktree button (0066 D#2 §3-5). Pure:
// the session hands in what it read, this picks the first rule of the priority table that hits.
// T#6 holds the file and commit columns; the merge / follow-up push / fetch columns are T#7's.
//
// Reasons come only from server values (E1 blockers / counts / row flags) and the screen's own
// state (read-only, 403, an action running). A waiting approval is never a reason (D#1 Q1).
import type { WorktreeStatus } from './types'

/** The buttons of T#6. `unstageAll` is every full unstage entry (quick button, 🔒 box, link). */
export type WorktreeButton = 'stage' | 'unstage' | 'unstageAll' | 'commit' | 'ai'

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
}

export interface WorktreeReason {
  disabled: boolean
  // null with `disabled` = in progress (no text, a progress mark only).
  code: string | null
}

const OK: WorktreeReason = { disabled: false, code: null }
const BUSY: WorktreeReason = { disabled: true, code: null }
const off = (code: string): WorktreeReason => ({ disabled: true, code })

// The E1 blocker operation a button stands for (server worktree_manual OPERATIONS + suggest).
const OPERATION: Record<WorktreeButton, readonly string[]> = {
  stage: ['stage', 'revert', 'remove'],
  unstage: ['unstage'],
  unstageAll: ['unstage'],
  commit: ['commit'],
  ai: ['suggest'],
}

/** The first rule of §3-5 that applies to `button`, or enabled. */
export function worktreeReason(button: WorktreeButton, input: WorktreeReasonInput): WorktreeReason {
  // 1–3: read-only, a 403 seen in this session, an action running.
  if (input.readOnly) return off('read_only')
  if (input.forbidden) return off('forbidden')
  if (input.busy) return BUSY
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
  }
}

/** The i18n key and values of a reason's short line; null = no text (enabled or in progress). */
export function reasonMessage(reason: WorktreeReason): { key: string; params: Record<string, string> } | null {
  if (!reason.disabled || !reason.code) return null
  if (WORKTREE_REASON_CODES.includes(reason.code)) return { key: `gui.git.reason.${reason.code}`, params: {} }
  return { key: 'gui.git.reason.unknown', params: { code: reason.code } }
}
