// flowgate.v02.0048 T#3 — skeleton of the review document API (D#1 §6.3). Owned by T#6 (actions), which
// fills this file only: request functions, their types and the snake_case -> camelCase
// normalization, every export name starting with `review`. index.ts already re-exports it.
//
// flowgate.v02.0048 T#6 — the Inspector header actions' calls, all existing v0.1 endpoints (NR0003
// §2.3 / §2.5, no new server API):
//   approve / reject   POST /api/v1/documents/{doc_id}/review_transitions/{approve|reject}
//                      body { comment } (reject: the reason, required -> rejection_reason)
//                      -> { document, tr_commit? } (tr_commit only on a TR approval)
//   run / rework       POST /api/v1/ai-invoke/start  { project, module, group, doc_ref,
//                      action_scope: edit | rework, mode: single } -> { run_id, status, … }
//   AI running         GET  /api/v1/ai-invoke/active?group_id= -> { active, run_id, status, doc_ref, … }
//   document state     GET  /api/v1/documents/{doc_id} — the review status and `group_disposed`
//                      (true when the group holds a DC document) the availability rules read
// The rework prompt is rebuilt by the server from the rejection history, so the rework hint
// endpoint (GET /ai-invoke/rework-hint) is not called.
//
// Refusals keep the server's shape and are thrown as they are; reviewErrorOf reads them. The two
// families answer differently: review_transitions raises HTTPException (`detail` string, e.g.
// "group_disposed: …") or the mutation policy envelope (`error.code`, 423 GROUP_AI_RUN_LOCKED while
// an AI run owns the group); ai-invoke/start answers a flat `{ code, message }` (409
// run_in_progress …).
import { normalizeApiError } from '@shared/apiErrors'
import { getRequest, postRequest } from '@shared/api'
import { toDocumentRecord, type DocumentRecord } from './document'

const API = '/api/v1'

type Raw = Record<string, unknown>

const text = (value: unknown): string | null => (typeof value === 'string' && value ? value : null)
const record = (value: unknown): Raw => (value && typeof value === 'object' && !Array.isArray(value) ? (value as Raw) : {})

/** A document with the facts the header actions decide on. */
export interface ReviewDocument {
  readonly record: DocumentRecord
  /** The document's group was disposed (a DC document exists); every change is refused. */
  readonly groupDisposed: boolean
}

/** The v0.1 document response -> ReviewDocument. Throws when it carries no doc_id. */
export function toReviewDocument(raw: unknown): ReviewDocument {
  return { record: toDocumentRecord(raw), groupDisposed: record(raw).group_disposed === true }
}

/** Reads one document's review state. Transport errors are thrown as they are. */
export async function reviewDocumentRead(docId: string): Promise<ReviewDocument> {
  const response = await getRequest<unknown>(`${API}/documents/${encodeURIComponent(docId)}`)
  return toReviewDocument(response.data)
}

/** The TR approval commit the server reports (shown as sent, never decided on). */
export interface ReviewTrCommit {
  readonly committed: boolean
  readonly commit: string | null
  readonly subject: string | null
  readonly skippedReason: string | null
}

export interface ReviewTransitionResult {
  readonly document: DocumentRecord
  /** Present on a TR approval only. */
  readonly trCommit: ReviewTrCommit | null
}

function toReviewTrCommit(raw: unknown): ReviewTrCommit | null {
  if (!raw || typeof raw !== 'object') return null
  const row = raw as Raw
  return {
    committed: row.committed === true,
    commit: text(row.commit) ?? text(row.commit_sha),
    subject: text(row.subject),
    skippedReason: text(row.skipped_reason),
  }
}

/** The review_transitions response -> ReviewTransitionResult. Throws without a document. */
export function toReviewTransitionResult(raw: unknown): ReviewTransitionResult {
  const row = record(raw)
  return { document: toDocumentRecord(row.document), trCommit: toReviewTrCommit(row.tr_commit) }
}

async function reviewTransition(docId: string, action: 'approve' | 'reject', comment?: string): Promise<ReviewTransitionResult> {
  const response = await postRequest<unknown>(
    `${API}/documents/${encodeURIComponent(docId)}/review_transitions/${action}`,
    comment === undefined ? {} : { comment },
  )
  return toReviewTransitionResult(response.data)
}

/** Approves a document under review (pending_review / revised -> approved). */
export function reviewApprove(docId: string): Promise<ReviewTransitionResult> {
  return reviewTransition(docId, 'approve')
}

/** Rejects a document under review with its reason (-> rejected, the reason joins the history). */
export function reviewReject(docId: string, reason: string): Promise<ReviewTransitionResult> {
  return reviewTransition(docId, 'reject', reason)
}

/** `project.module.group` -> its parts; null when the id is not a v0.1 group id. */
export function reviewGroupParts(groupId: string): { project: string; module: string; group: string } | null {
  const parts = groupId.split('.')
  if (parts.length < 3 || parts.some((part) => part === '')) return null
  return { project: parts[0], module: parts[1], group: parts.slice(2).join('.') }
}

/** What a v0.1 single run does: edit = the AI works on the document again, rework = after a rejection. */
export type ReviewRunScope = 'edit' | 'rework'

export interface ReviewRunRequest {
  /** The Flow = v0.1 group id the document belongs to. */
  readonly groupId: string
  readonly docId: string
  readonly scope: ReviewRunScope
}

export interface ReviewRun {
  readonly runId: string | null
  readonly status: string | null
}

/** Starts one v0.1 single AI run on the document (the server picks the provider). */
export async function reviewRunStart(request: ReviewRunRequest): Promise<ReviewRun> {
  const parts = reviewGroupParts(request.groupId)
  if (!parts) throw new Error(`not a group id: ${request.groupId}`)
  const response = await postRequest<unknown>(`${API}/ai-invoke/start`, {
    project: parts.project,
    module: parts.module,
    group: parts.group,
    doc_ref: request.docId,
    action_scope: request.scope,
    mode: 'single',
  })
  const row = record(response.data)
  return { runId: text(row.run_id), status: text(row.status) }
}

/** The group's live AI run, if any. */
export interface ReviewActivity {
  readonly active: boolean
  readonly runId: string | null
  readonly status: string | null
  /** The document the run works on. */
  readonly docRef: string | null
  readonly actionScope: string | null
}

/** The /ai-invoke/active response -> ReviewActivity (a finished run is not active). */
export function toReviewActivity(raw: unknown): ReviewActivity {
  const row = record(raw)
  const status = text(row.status)
  return { active: row.active === true && status !== 'finished', runId: text(row.run_id), status, docRef: text(row.doc_ref), actionScope: text(row.action_scope) }
}

/** Reads whether an AI run owns the group now. */
export async function reviewActivityRead(groupId: string): Promise<ReviewActivity> {
  const response = await getRequest<unknown>(`${API}/ai-invoke/active`, { group_id: groupId })
  return toReviewActivity(response.data)
}

/**
 * Why a review call was refused:
 * running = an AI run owns the group (423 GROUP_AI_RUN_LOCKED, 409 run_in_progress);
 * disposed = the group was disposed; conflict = another 409 (the state moved, not the workflow
 * head, a gate); forbidden 403; not_found 404; invalid 400 / 422; network = no answer.
 */
export type ReviewErrorKind = 'running' | 'disposed' | 'conflict' | 'forbidden' | 'not_found' | 'invalid' | 'network' | 'other'

export interface ReviewError {
  readonly kind: ReviewErrorKind
  readonly status: number | null
  readonly code: string | null
}

const RUNNING_CODES = new Set(['GROUP_AI_RUN_LOCKED', 'run_in_progress', 'run_still_live'])

/** What a failed review call threw, as kind / status / code. Only code and status are read. */
export function reviewErrorOf(error: unknown): ReviewError {
  const normalized = normalizeApiError(error)
  const data = record(record(record(error).response).data)
  // ai-invoke answers a flat `{ code }`; review_transitions an `error` / `detail` object or a detail string.
  const code = normalized.code ?? (text(data.code)?.trim() || null)
  const detail = typeof data.detail === 'string' ? data.detail : ''
  const status = normalized.status
  let kind: ReviewErrorKind
  if (normalized.network) kind = 'network'
  else if (status === 423 || (code !== null && RUNNING_CODES.has(code))) kind = 'running'
  else if (code === 'group_disposed' || detail.startsWith('group_disposed')) kind = 'disposed'
  else if (status === 409) kind = 'conflict'
  else if (status === 403) kind = 'forbidden'
  else if (status === 404) kind = 'not_found'
  else if (status === 400 || status === 422) kind = 'invalid'
  else kind = 'other'
  return { kind, status, code }
}
