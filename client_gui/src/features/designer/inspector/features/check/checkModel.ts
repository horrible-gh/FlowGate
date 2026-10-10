// flowgate.v02.0048 T#12 — the check card's rules (D#1 §8, §10.1 확인·Git column, §10.2 T#12 row). Pure
// functions: card kind × review status × read only × AI running × group disposed × the group's final
// approval job × an action in flight -> per operation visible / enabled / reason; the review and Git
// reflection state -> display keys; the Git choice the final approval may ride on; a final approval's
// answer -> the line shown after it.
//
// Operations (D#1 §8.3 rule 0):
//   approve   change: the v0.1 final approval of an AC under review (pending_review · revised), with
//             the Git choice the server offers. Every ownership (a document function, rules 2-4).
//   openGit   query: the right Tool Panel on Git (Host intent `show-tool: git`), where Git is
//             reflected in detail. Always usable, also read only.
//   open      query: ↗ 열기, the v0.1 AC screen (time-machine reopen / restore, merge target).
// Not offered at all (D#1 §8.4): Graph gate confirm / reject, AI delegation's last check, other
// uses of the check card — no disabled place-holder either.
//
// Permissions are the server's (403 is shown as a reason); no operation here is admin only.
import type { InspectorCardKind, InspectorReadOnly } from '../../inspectorTarget'

export type CheckOperation = 'approve' | 'openGit' | 'open'

export const CHECK_OPERATIONS: readonly CheckOperation[] = ['approve', 'openGit', 'open']

export const CHECK_OPERATION_CLASS: Readonly<Record<CheckOperation, 'change' | 'query'>> = {
  approve: 'change',
  openGit: 'query',
  open: 'query',
}

/** Review statuses the server approves from (DOC_REVIEW_TRANSITIONS). */
export const CHECK_UNDER_REVIEW: readonly string[] = ['pending_review', 'revised']

/** Git choices of the final approval (the server's FINALIZE_MAIN_CHOICES). */
export type CheckChoice = 'merge' | 'merge_only' | 'wait'

/** The parts of the Git reflection state these rules read (`@/features/document` CheckGitState). */
export interface CheckGitFacts {
  readonly status: string
  readonly defaultAction: string | null
  readonly choices: readonly CheckChoice[]
  readonly aheadCount: number | null
  readonly behindCount: number | null
  readonly mergeCommit: string | null
  readonly approvalPending: boolean
  readonly approvalInFlight: boolean | null
  readonly approvalJob: { readonly jobId: string | null; readonly status: string | null; readonly stage: string | null; readonly docId: string | null } | null
}

export interface CheckInput {
  readonly cardKind: InspectorCardKind
  readonly readOnly: InspectorReadOnly
  /** The latest known review status of the AC (the read document, else the Canvas). */
  readonly docReviewStatus: string | null
  /** An AI run owns the Flow's group. */
  readonly aiRunning: boolean
  readonly groupDisposed: boolean
  /** The group's final approval is still being worked on by the server (checkApprovalRunning). */
  readonly approvalRunning: boolean
  /** The final approval is waiting for its answer. */
  readonly busy: boolean
}

export interface CheckAvailabilityItem {
  readonly visible: boolean
  readonly enabled: boolean
  /** Message key (under `gui.inspector.check.`) of why it is disabled, or null. */
  readonly reason: string | null
}

export type CheckAvailability = Readonly<Record<CheckOperation, CheckAvailabilityItem>>

const HIDDEN: CheckAvailabilityItem = { visible: false, enabled: false, reason: null }
const USABLE: CheckAvailabilityItem = { visible: true, enabled: true, reason: null }

/**
 * Why the final approval is disabled: one reason, in the order of D#1 §8.3 rule 6-1 — ownership
 * conflict, revision view, disposed, AI running, then this feature's own (the server still works on
 * this group's final approval, then an approval in flight here).
 */
export function checkDisabledReason(input: CheckInput): string | null {
  if (input.readOnly === 'conflict') return 'reason.read_only_conflict'
  if (input.readOnly === 'revision') return 'reason.read_only_revision'
  if (input.groupDisposed) return 'reason.disposed'
  if (input.aiRunning) return 'reason.ai_running'
  if (input.approvalRunning) return 'reason.approval_running'
  if (input.busy) return 'reason.busy'
  return null
}

/** Per operation: shown at all, usable now, and why not. */
export function checkAvailability(input: CheckInput): CheckAvailability {
  if (input.cardKind !== 'check') return { approve: HIDDEN, openGit: HIDDEN, open: HIDDEN }
  const reason = checkDisabledReason(input)
  const approve = CHECK_UNDER_REVIEW.includes(input.docReviewStatus ?? '') ? { visible: true, enabled: reason === null, reason } : HIDDEN
  return { approve, openGit: USABLE, open: USABLE }
}

/** The review status the rules read: the read document's when it is the target's, else the Canvas one. */
export function checkReviewStatus(
  target: { readonly docId: string | null; readonly docReviewStatus: string | null },
  read: { readonly docId: string; readonly docReviewStatus: string | null } | null,
): string | null {
  return read && read.docId === target.docId ? read.docReviewStatus : target.docReviewStatus
}

/** Message key of the AC's check state. */
export function checkReviewKey(status: string | null): string {
  if (status === null) return 'review.unknown'
  if (CHECK_UNDER_REVIEW.includes(status)) return 'review.pending'
  if (status === 'approved' || status === 'wf_done') return 'review.approved'
  if (status === 'rejected') return 'review.rejected'
  return 'review.other'
}

/** Git reflection statuses with their own wording; anything else is shown as sent. */
export const CHECK_GIT_STATUSES = ['none', 'awaiting_choice', 'waiting', 'merging', 'conflict', 'merged', 'pushed', 'discarded', 'archived', 'stashed'] as const

export type CheckTone = 'muted' | 'warning' | 'danger' | 'success'

const GIT_TONE: Readonly<Record<string, CheckTone>> = {
  awaiting_choice: 'warning',
  waiting: 'warning',
  merging: 'muted',
  conflict: 'danger',
  merged: 'success',
  pushed: 'success',
}

/** Git reflection state -> message key, the status sent (for an unknown one) and a tone. */
export function checkGitLine(status: string): { readonly key: string; readonly status: string; readonly tone: CheckTone } {
  const known = (CHECK_GIT_STATUSES as readonly string[]).includes(status)
  return { key: known ? `git.status.${status}` : 'git.status.other', status, tone: GIT_TONE[status] ?? 'muted' }
}

/** The line under the Git state: ahead / behind, the merge commit, or why approval still waits. */
export function checkGitNotes(git: CheckGitFacts): readonly { readonly key: string; readonly params: Readonly<Record<string, string | number>> }[] {
  const notes: { key: string; params: Record<string, string | number> }[] = []
  if (git.approvalPending) notes.push({ key: 'git.approval_pending', params: {} })
  if (git.mergeCommit && (git.status === 'merged' || git.status === 'pushed')) notes.push({ key: 'git.merge_commit', params: { commit: git.mergeCommit.slice(0, 10) } })
  if (git.status !== 'none' && git.aheadCount !== null && git.behindCount !== null) {
    notes.push({ key: 'git.ahead_behind', params: { ahead: git.aheadCount, behind: git.behindCount } })
  }
  return notes
}

/** The final approval job of this AC that the server still works on, or null. */
function liveJobOf(git: CheckGitFacts, docId: string | null) {
  const job = git.approvalJob
  if (!job || (job.docId !== null && job.docId !== docId)) return null
  return job
}

const isRecovery = (job: { readonly status: string | null; readonly stage: string | null }) =>
  job.stage === 'recovery_required' || job.status === 'recovery_required'

/**
 * The server still works on this group's final approval: its lock is held (approval_in_flight true)
 * or this AC's job waits. A job parked in recovery_required leaves the retry to the person (the same
 * final approval), so it does not block.
 */
export function checkApprovalRunning(git: CheckGitFacts | null, docId: string | null): boolean {
  if (!git) return false
  const job = liveJobOf(git, docId)
  if (job && isRecovery(job)) return false
  return git.approvalInFlight === true || job !== null
}

/** The line of this AC's waiting final approval job, or null. */
export function checkJobLine(git: CheckGitFacts | null, docId: string | null): { readonly key: string; readonly params: Readonly<Record<string, string>> } | null {
  const job = git ? liveJobOf(git, docId) : null
  if (!job) return null
  const params = { job: job.jobId ?? '-', stage: job.stage ?? job.status ?? '-' }
  return { key: isRecovery(job) ? 'job.recovery' : 'job.queued', params }
}

/** The Git choice is asked: the slot awaits a choice and the server offers some. */
export function checkGitOffered(git: CheckGitFacts | null): boolean {
  return !!git && (git.status === 'awaiting_choice' || git.status === 'waiting') && git.choices.length > 0
}

// The v0.1 confirm dialog starts on the default action without a push (actionOfPosition(…, false, …)).
const NO_PUSH: Readonly<Record<string, CheckChoice>> = { merge: 'merge_only', commit_push: 'wait', commit_only: 'wait', push: 'wait' }

/** The choice the dialog starts on: the project default without its push, when offered; else wait. */
export function checkDefaultChoice(git: CheckGitFacts): CheckChoice | null {
  if (!git.choices.length) return null
  const wanted = NO_PUSH[git.defaultAction ?? ''] ?? (git.defaultAction as CheckChoice | null) ?? 'wait'
  if (git.choices.includes(wanted)) return wanted
  return git.choices.includes('wait') ? 'wait' : git.choices[0]
}

/** What a final approval answered (`@/features/document` CheckApproveResult). */
export interface CheckOutcomeFacts {
  readonly approved: boolean
  readonly stage: string | null
  readonly jobId: string | null
  readonly git: {
    readonly ok: boolean
    readonly quiet: boolean
    readonly status: string | null
    readonly mergeCommit: string | null
    readonly pushed: boolean | null
    readonly conflictFiles: number
    readonly errorCode: string | null
  } | null
}

export interface CheckOutcomeLine {
  readonly key: string
  readonly params: Readonly<Record<string, string | number>>
  readonly tone: CheckTone
  /** The Git panel is where this continues (conflict, a failed Git step, a recovery). */
  readonly toGit: boolean
}

/** A final approval's answer -> the line shown after it (the v0.1 ReviewActionBar's toasts). */
export function checkOutcome(result: CheckOutcomeFacts): CheckOutcomeLine {
  const git = result.git
  const line = (key: string, tone: CheckTone, toGit = false, params: Record<string, string | number> = {}): CheckOutcomeLine => ({ key, params, tone, toGit })
  if (!result.approved) {
    if (result.stage === 'queued') return line('outcome.queued', 'muted', false, { job: result.jobId ?? '-' })
    if (result.stage === 'recovery_required') return line('outcome.recovery', 'warning', true, { job: result.jobId ?? '-' })
    if (result.stage === 'git_finalize' || git?.status === 'conflict') return line('outcome.conflict', 'warning', true, { n: git?.conflictFiles ?? 0 })
    return line('outcome.not_approved', 'warning', git?.ok === false)
  }
  if (!git || git.quiet) return line('outcome.approved', 'success')
  if (!git.ok) return line('outcome.git_failed', 'warning', true)
  const commit = (git.mergeCommit ?? '').slice(0, 10)
  switch (git.status) {
    case 'merged':
      return git.pushed === false ? line('outcome.merged_local', 'success', false, { commit }) : line('outcome.merged', 'success', false, { commit })
    case 'pushed':
      return line('outcome.pushed', 'success')
    case 'waiting':
      return line('outcome.waiting', 'muted')
    case 'conflict':
      return line('outcome.conflict', 'warning', true, { n: git.conflictFiles })
    case 'archived':
    case 'stashed':
      return line('outcome.archived', 'success')
    default:
      return line('outcome.approved', 'success')
  }
}

/** Kinds of a refused call (the `@/features/document` checkErrorOf kinds). */
export type CheckFailureKind =
  | 'running'
  | 'disposed'
  | 'conflict'
  | 'forbidden'
  | 'not_found'
  | 'invalid'
  | 'network'
  | 'other'
  | 'git_busy'
  | 'stale_choice'
  | 'precheck'

/** Message key of a refused call; the state is read again after it is shown. */
export function checkFailureKey(kind: CheckFailureKind): string {
  return `failure.${kind}`
}

/** Message key of a failed Git state read (documentErrorOf kinds): permission, or any other failure. */
export function checkGitErrorKey(kind: string): string {
  return kind === 'forbidden' ? 'git.read_forbidden' : 'git.read_failed'
}

// --- flowgate.v02.0051 stage 2 — the v7 확인 결과 result line (0035 cmid.* / cend.*) -------------------------

export type CheckSlotTone = 'neutral' | 'info' | 'success' | 'warning' | 'danger' | 'running' | 'hold'

/** The tone of the 확인 결과 pill for a review status: waiting is the human's turn (amber), approved green, rejected red. */
export function checkReviewTone(status: string | null): CheckSlotTone {
  if (status === null) return 'neutral'
  if (CHECK_UNDER_REVIEW.includes(status)) return 'warning'
  if (status === 'approved') return 'success'
  if (status === 'rejected') return 'danger'
  return 'neutral'
}

/** The pill tone of the Git line on the result slot: the check feature's tone names mapped to the chip's. */
export function checkGitTone(tone: CheckTone): CheckSlotTone {
  switch (tone) {
    case 'warning':
      return 'warning'
    case 'danger':
      return 'danger'
    case 'success':
      return 'success'
    default:
      return 'neutral'
  }
}
