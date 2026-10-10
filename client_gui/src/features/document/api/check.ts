// flowgate.v02.0048 T#3 — skeleton of the check document API (D#1 §6.3). Owned by T#12 (check), which
// fills this file only: request functions, their types and the snake_case -> camelCase
// normalization, every export name starting with `check`. index.ts already re-exports it. The
// v0.1 endpoints it will wrap (NR0003 §2):
//   final approval   POST /api/v1/documents/workflow/final-approval  { doc_id }
//   approve (AC)     POST /api/v1/documents/review_transitions/approve  { doc_id, git_action, git_target_branch? }
//   Git context      GET  /api/v1/groups/{gid}/git/finalize?context=approval
//   reopen/restore   POST /api/v1/documents/workflow/reopen · /restore
//
// flowgate.v02.0048 T#12 — the check card's calls, all existing v0.1 endpoints (NR0003 §2.8, no new
// server API):
//   AC state         GET  /api/v1/documents/{doc_id} — the review status and `group_disposed`
//   Git context      GET  /api/v1/groups/{gid}/git/finalize?context=approval -> { ok, state }
//                    the group's Git reflection state; while the AC still waits it carries the
//                    preliminary `awaiting_choice` and the main choices (merge · merge_only · wait)
//                    the final approval may ride on (the v0.1 AC confirm dialog reads the same).
//   final approval   POST /api/v1/documents/review_transitions/approve
//                    { doc_id, comment: null, git_action? } -> { document?, git?, approval?, job? }
//                    git_action only for a choice that changes Git (merge · merge_only); `wait`
//                    and no choice approve without it, the server then leaves the slot waiting.
// Not wrapped (the v0.1 screen keeps them, reached by ↗ 열기): creating the AC (final-approval —
// an AC Node exists only once its document does), the time-machine reopen / restore an AC
// rejection goes through (a step picker with a TR commit preview), the merge target picker
// (`git_target_branch`, finalize-target) and the push / commit axes. Git work beyond the choice is
// the Git panel's (the Git feature's part, opened through the Host intent `show-tool: git`).
//
// Refusals keep the server's shape and are thrown as they are; checkErrorOf reads them on top of
// reviewErrorOf (the same review_transitions family) and names the final approval's own codes.
import { getRequest, postRequest } from '@shared/api'
import { toDocumentRecord, type DocumentRecord } from './document'
import { reviewErrorOf, toReviewDocument, type ReviewDocument, type ReviewErrorKind } from './review'

const API = '/api/v1'

type Raw = Record<string, unknown>

const text = (value: unknown): string | null => (typeof value === 'string' && value ? value : null)
const record = (value: unknown): Raw => (value && typeof value === 'object' && !Array.isArray(value) ? (value as Raw) : {})
const count = (value: unknown): number | null => (typeof value === 'number' && Number.isFinite(value) ? value : null)
const texts = (value: unknown): string[] => (Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string' && item !== '') : [])

/**
 * Reads the AC's review state (GET /documents/{doc_id}: status, group disposed). The check card's own
 * call — the normalization is review's (toReviewDocument) — so the header actions' reads and the
 * check card's never stand in for each other (D#1 §12.5 item 1).
 */
export async function checkDocumentRead(docId: string): Promise<ReviewDocument> {
  const response = await getRequest<unknown>(`${API}/documents/${encodeURIComponent(docId)}`)
  return toReviewDocument(response.data)
}

/** Git choices a final approval may ride on, as the server offers them (FINALIZE_MAIN_CHOICES). */
export const CHECK_GIT_CHOICES = ['merge', 'merge_only', 'wait'] as const

export type CheckGitChoice = (typeof CHECK_GIT_CHOICES)[number]

/** Choices that are sent as `git_action`; `wait` approves without one. */
export const CHECK_GIT_ACTIONS: readonly CheckGitChoice[] = ['merge', 'merge_only']

/** The group's final approval job, as the server reports it (approval_publish.job_view). */
export interface CheckApprovalJob {
  readonly jobId: string | null
  readonly status: string | null
  readonly stage: string | null
  /** The AC the job approves. */
  readonly docId: string | null
}

/** The group's Git reflection state for the final approval. */
export interface CheckGitState {
  readonly groupId: string | null
  /**
   * none (no Git work: not Git active, nothing to reflect, or already cleaned up) · awaiting_choice ·
   * waiting · merging · conflict · merged · pushed · discarded · archived · stashed, or what the server sent.
   */
  readonly status: string
  readonly branch: string | null
  readonly baseBranch: string | null
  /** The merge target the server uses when none is sent (the group's work base). */
  readonly workBaseRef: string | null
  readonly defaultAction: string | null
  /** Choices offered now (empty unless the slot awaits a choice), known values only, server order. */
  readonly choices: readonly CheckGitChoice[]
  readonly aheadCount: number | null
  readonly behindCount: number | null
  readonly mergeCommit: string | null
  /** Git finished but its approval still has to be committed (the same approval retries it). */
  readonly approvalPending: boolean
  /** A final approval is running Git now: true · false · null (the server could not tell). */
  readonly approvalInFlight: boolean | null
  readonly approvalJob: CheckApprovalJob | null
}

function toCheckApprovalJob(raw: unknown): CheckApprovalJob | null {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return null
  const row = raw as Raw
  return { jobId: text(row.job_id), status: text(row.status), stage: text(row.stage), docId: text(row.doc_id) }
}

/** The finalize response (`{ ok, state }`, or the state itself) -> CheckGitState. */
export function toCheckGitState(raw: unknown): CheckGitState {
  const outer = record(raw)
  const row = outer.state && typeof outer.state === 'object' ? record(outer.state) : outer
  const inFlight = row.approval_in_flight
  return {
    groupId: text(row.group_id),
    status: text(row.status) ?? 'none',
    branch: text(row.branch),
    baseBranch: text(row.base_branch),
    workBaseRef: text(row.default_target) ?? text(row.work_base_ref) ?? text(row.base_branch),
    defaultAction: text(row.default_action),
    choices: CHECK_GIT_CHOICES.filter((choice) => texts(row.choices).includes(choice)),
    aheadCount: count(row.ahead_count),
    behindCount: count(row.behind_count),
    mergeCommit: text(row.merge_commit),
    approvalPending: row.approval_pending === true,
    approvalInFlight: typeof inFlight === 'boolean' ? inFlight : null,
    approvalJob: toCheckApprovalJob(row.approval_job),
  }
}

/** Reads the group's Git reflection state as the final approval sees it. Transport errors are thrown. */
export async function checkGitRead(groupId: string): Promise<CheckGitState> {
  const response = await getRequest<unknown>(`${API}/groups/${encodeURIComponent(groupId)}/git/finalize`, { context: 'approval' })
  return toCheckGitState(response.data)
}

/** What the Git step of a final approval did (shown as sent, never decided on). */
export interface CheckApproveGit {
  /** false: the Git step failed (the approval may still stand). */
  readonly ok: boolean
  /** The server proved there was nothing to reflect. */
  readonly quiet: boolean
  /** merged · pushed · waiting · conflict · archived …, or null. */
  readonly status: string | null
  readonly mergeCommit: string | null
  /** false: merged locally, not pushed. */
  readonly pushed: boolean | null
  readonly conflictFiles: number
  readonly errorCode: string | null
}

export interface CheckApproveResult {
  /** The AC is approved now (the root moves to wf_done). */
  readonly approved: boolean
  /**
   * Why it is not approved yet: queued / recovery_required (the approval job finishes it later),
   * git_finalize (Git was handed to a conflict review), or null.
   */
  readonly stage: string | null
  readonly jobId: string | null
  readonly git: CheckApproveGit | null
  /** The AC as the server answered it, when it sent one. */
  readonly document: DocumentRecord | null
}

function toCheckApproveGit(raw: unknown): CheckApproveGit | null {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return null
  const row = raw as Raw
  const result = record(row.result)
  return {
    ok: row.ok !== false,
    quiet: row.quiet === true,
    status: text(result.status),
    mergeCommit: text(result.merge_commit),
    pushed: typeof result.pushed === 'boolean' ? result.pushed : null,
    conflictFiles: Array.isArray(result.conflict_files) ? result.conflict_files.length : 0,
    errorCode: text(record(row.error).code),
  }
}

/** The approve response -> CheckApproveResult (the v0.1 ReviewActionBar reads it the same way). */
export function toCheckApproveResult(raw: unknown): CheckApproveResult {
  const row = record(raw)
  const approval = row.approval && typeof row.approval === 'object' ? record(row.approval) : null
  let document: DocumentRecord | null = null
  try {
    document = row.document ? toDocumentRecord(row.document) : null
  } catch {
    document = null
  }
  // An answer without the approval envelope is the plain transition: the document tells.
  const approved = approval ? approval.approved === true : document?.docReviewStatus === 'approved'
  return {
    approved,
    stage: approved ? null : text(approval?.stage),
    jobId: text(approval?.job_id) ?? text(record(row.job).job_id),
    git: toCheckApproveGit(row.git),
    document,
  }
}

export interface CheckApproveRequest {
  /** The AC document. */
  readonly docId: string
  /** The Git choice: merge / merge_only ride along as git_action; wait or null approve without one. */
  readonly choice: CheckGitChoice | null
}

/** Final approval of an AC (pending_review / revised), with the Git choice when it changes Git. */
export async function checkApprove(request: CheckApproveRequest): Promise<CheckApproveResult> {
  const body: Record<string, unknown> = { doc_id: request.docId, comment: null }
  if (request.choice && CHECK_GIT_ACTIONS.includes(request.choice)) body.git_action = request.choice
  const response = await postRequest<unknown>(`${API}/documents/review_transitions/approve`, body)
  return toCheckApproveResult(response.data)
}

/**
 * Why a check call was refused: the review kinds (running · disposed · conflict · forbidden ·
 * not_found · invalid · network · other) plus the final approval's own —
 * git_busy = another Git operation holds the group / target (409 git_busy, merge_target_busy);
 * stale_choice = the Git choice no longer applies (422 invalid_request: the slot moved);
 * precheck = the approval itself is refused now (409 approval_precheck_failed, 409 retry mismatch).
 */
export type CheckErrorKind = ReviewErrorKind | 'git_busy' | 'stale_choice' | 'precheck'

export interface CheckError {
  readonly kind: CheckErrorKind
  readonly status: number | null
  readonly code: string | null
}

const GIT_BUSY_CODES = new Set(['git_busy', 'merge_target_busy'])
const PRECHECK_CODES = new Set(['approval_precheck_failed', 'final_approval_retry_mismatch'])

/** What a failed check call threw, as kind / status / code. Only code and status are read. */
export function checkErrorOf(error: unknown): CheckError {
  const base = reviewErrorOf(error)
  const { status, code } = base
  let kind: CheckErrorKind = base.kind
  if (code !== null && GIT_BUSY_CODES.has(code)) kind = 'git_busy'
  // A permission refusal of the precheck (403) stays `forbidden`.
  else if (status === 409 && code !== null && PRECHECK_CODES.has(code)) kind = 'precheck'
  else if (status === 422 && code === 'invalid_request') kind = 'stale_choice'
  return { kind, status, code }
}
