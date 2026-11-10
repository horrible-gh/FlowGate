// flowgate.chat.0017 CH0002 — AI attachment upload requests: the user side (server
// `api/v1/attachment_request_routes.py`). An AI run hands a file over; it waits here, staged, until a person
// previews / downloads it and approves (it is then registered as an attachment of the card's live document) or
// rejects it. Requests outlive their AI run, so every screen reads them from the server, never from a run.
//   list (chat)   GET  /api/v1/attachment-requests?chat_doc_id=          -> { groups: [{ run_id, anchor_*, requests }] }
//   list (card)   GET  /api/v1/attachment-requests?flow_id=&node_key=    -> { requests, pending }
//   counts        GET  /api/v1/attachment-requests/counts?flow_id=       -> { total, by_node }
//   limits        GET  /api/v1/attachment-requests/limits?project_id=    -> { values, sources }
//   preview       GET  /api/v1/attachment-requests/{id}/preview          -> { kind: text|image|binary, content, ... }
//   content       GET  /api/v1/attachment-requests/{id}/content[?inline=1] (blob)
//   decision      POST /api/v1/attachment-requests/{id}/decision         { decision, expected_doc_id, conflict, expected_existing_sha256 }
//   bulk          POST /api/v1/attachment-requests/decisions             { decision, items: [{ request_id, expected_doc_id }] }
//   settings      GET/PUT /api/v1/settings/attachment-requests, /api/v1/projects/{id}/attachment-request-settings
// Refusals keep the server's shape `{ ok: false, error: { code, message, details } }` and are thrown as they are;
// attachmentRequestsErrorOf reads them.
import { normalizeApiError } from '@shared/apiErrors'
import { downloadBlobRequest, getRequest, postRequest, putRequest } from '@shared/api'

const API = '/api/v1'

type Raw = Record<string, unknown>

const text = (value: unknown): string | null => (typeof value === 'string' && value ? value : null)
const record = (value: unknown): Raw => (value && typeof value === 'object' && !Array.isArray(value) ? (value as Raw) : {})
const list = (value: unknown): unknown[] => (Array.isArray(value) ? value : [])
const whole = (value: unknown): number | null => (typeof value === 'number' && Number.isInteger(value) && value >= 0 ? value : null)

export type AttachmentRequestStatus = 'pending' | 'approving' | 'approved' | 'rejected' | 'expired' | 'failed'
export type AttachmentRequestConflictMode = 'keep_both' | 'overwrite'

/** A same-name attachment already on the request's target document. */
export interface AttachmentRequestConflict {
  readonly docId: string
  readonly filename: string
  /** Only the letter case differs: overwriting is refused, keep both is the way. */
  readonly caseOnly: boolean
  readonly size: number
  readonly sha256: string | null
}

export interface AttachmentRequest {
  readonly id: string
  readonly origin: 'chat' | 'card' | string
  readonly status: AttachmentRequestStatus | string
  readonly projectId: string | null
  readonly flowId: string | null
  readonly nodeKey: string | null
  readonly targetDocId: string | null
  readonly targetTitle: string | null
  readonly runId: string | null
  readonly providerName: string | null
  readonly filename: string
  readonly originalFilename: string | null
  readonly size: number
  readonly contentType: string | null
  readonly sha256: string | null
  readonly note: string | null
  readonly decision: AttachmentRequestConflictMode | null
  readonly decidedBy: string | null
  readonly decidedAt: string | null
  readonly finalDocId: string | null
  readonly finalFilename: string | null
  readonly errorCode: string | null
  readonly expiresAt: string | null
  readonly createdAt: string | null
  readonly preview: 'text' | 'image' | null
  readonly conflict: AttachmentRequestConflict | null
}

export const ATTACHMENT_REQUEST_OPEN: readonly string[] = ['pending', 'approving']

export function attachmentRequestIsOpen(request: Pick<AttachmentRequest, 'status'>): boolean {
  return ATTACHMENT_REQUEST_OPEN.includes(request.status)
}

function toConflict(raw: unknown): AttachmentRequestConflict | null {
  const row = record(raw)
  const docId = text(row.doc_id)
  const filename = text(row.filename)
  if (!docId || !filename) return null
  return { docId, filename, caseOnly: row.case_only === true, size: whole(row.size) ?? 0, sha256: text(row.content_sha256) }
}

export function toAttachmentRequest(raw: unknown): AttachmentRequest | null {
  const row = record(raw)
  const id = text(row.request_id)
  const filename = text(row.filename)
  if (!id || !filename) return null
  const decision = text(row.decision)
  const preview = text(row.preview)
  return {
    id,
    origin: text(row.origin) ?? 'chat',
    status: text(row.status) ?? 'pending',
    projectId: text(row.project_id),
    flowId: text(row.flow_id),
    nodeKey: text(row.node_key),
    targetDocId: text(row.target_doc_id),
    targetTitle: text(row.target_title),
    runId: text(row.ai_run_id),
    providerName: text(row.provider_name),
    filename,
    originalFilename: text(row.original_filename),
    size: whole(row.size) ?? 0,
    contentType: text(row.content_type),
    sha256: text(row.content_sha256),
    note: text(row.note),
    decision: decision === 'keep_both' || decision === 'overwrite' ? decision : null,
    decidedBy: text(row.decided_by),
    decidedAt: text(row.decided_at),
    finalDocId: text(row.final_doc_id),
    finalFilename: text(row.final_filename),
    errorCode: text(row.error_code),
    expiresAt: text(row.expires_at),
    createdAt: text(row.created_at),
    preview: preview === 'image' ? 'image' : preview === 'text' ? 'text' : null,
    conflict: toConflict(row.conflict),
  }
}

const requestsOf = (value: unknown): AttachmentRequest[] => list(value).flatMap((item) => toAttachmentRequest(item) ?? [])

/** One run's requests in a chat, placed after that run's AI reply (the server's anchor). */
export interface AttachmentRequestGroup {
  readonly id: string
  readonly runId: string | null
  readonly anchorSeq: number | null
  readonly anchorPosition: 'before' | 'after' | null
  readonly anchorState: string | null
  readonly createdAt: string | null
  readonly pending: number
  readonly requests: readonly AttachmentRequest[]
}

export function toAttachmentRequestGroup(raw: unknown): AttachmentRequestGroup | null {
  const row = record(raw)
  const id = text(row.id)
  if (!id) return null
  const position = text(row.anchor_position)
  const seq = row.anchor_seq
  return {
    id,
    runId: text(row.run_id),
    anchorSeq: typeof seq === 'number' && Number.isInteger(seq) ? seq : null,
    anchorPosition: position === 'before' || position === 'after' ? position : null,
    anchorState: text(row.anchor_state),
    createdAt: text(row.created_at),
    pending: whole(row.pending) ?? 0,
    requests: requestsOf(row.requests),
  }
}

export function toAttachmentRequestGroups(value: unknown): AttachmentRequestGroup[] {
  return list(value).flatMap((item) => toAttachmentRequestGroup(item) ?? [])
}

const base = `${API}/attachment-requests`
const one = (id: string) => `${base}/${encodeURIComponent(id)}`

export async function attachmentRequestsForChat(chatDocId: string): Promise<AttachmentRequestGroup[]> {
  const response = await getRequest<unknown>(base, { chat_doc_id: chatDocId })
  return toAttachmentRequestGroups(record(response.data).groups)
}

export interface AttachmentRequestsOfCard {
  readonly flowId: string
  readonly nodeKey: string
  readonly requests: readonly AttachmentRequest[]
  readonly pending: number
}

/** The open requests aimed at one card (Flow node), oldest first. */
export async function attachmentRequestsForCard(flowId: string, nodeKey: string): Promise<AttachmentRequestsOfCard> {
  const response = await getRequest<unknown>(base, { flow_id: flowId, node_key: nodeKey })
  const data = record(response.data)
  const requests = requestsOf(data.requests)
  return { flowId, nodeKey, requests, pending: whole(data.pending) ?? requests.filter(attachmentRequestIsOpen).length }
}

export interface AttachmentRequestCounts {
  readonly flowId: string
  readonly total: number
  readonly byNode: Readonly<Record<string, number>>
}

export async function attachmentRequestCounts(flowId: string): Promise<AttachmentRequestCounts> {
  const response = await getRequest<unknown>(`${base}/counts`, { flow_id: flowId })
  const data = record(response.data)
  const byNode: Record<string, number> = {}
  for (const [key, value] of Object.entries(record(data.by_node))) {
    const n = whole(value)
    if (n !== null) byNode[key] = n
  }
  return { flowId, total: whole(data.total) ?? 0, byNode }
}

export interface AttachmentRequestPreview {
  readonly kind: 'text' | 'image' | 'binary'
  readonly content: string | null
  readonly truncated: boolean
  readonly mediaType: string | null
}

export async function attachmentRequestPreview(id: string): Promise<AttachmentRequestPreview> {
  const response = await getRequest<unknown>(`${one(id)}/preview`)
  const data = record(response.data)
  const kind = text(data.kind)
  return {
    kind: kind === 'text' || kind === 'image' ? kind : 'binary',
    content: typeof data.content === 'string' ? data.content : null,
    truncated: data.truncated === true,
    mediaType: text(data.media_type),
  }
}

/** The staged bytes as a blob (``inline`` asks for a verified raster image's own media type). */
export async function attachmentRequestContent(id: string, inline = false): Promise<Blob> {
  try {
    const response = await downloadBlobRequest(`${one(id)}/content${inline ? '?inline=1' : ''}`)
    return response.data
  } catch (error) {
    await readBlobRefusal(error)
    throw error
  }
}

async function readBlobRefusal(error: unknown): Promise<void> {
  const response = record(record(error).response)
  const data = response.data
  if (typeof Blob === 'undefined' || !(data instanceof Blob)) return
  try {
    response.data = JSON.parse(typeof data.text === 'function' ? await data.text() : '')
  } catch {
    // Not JSON: the status alone is read.
  }
}

export interface AttachmentRequestApproval {
  readonly expectedDocId: string
  readonly conflict?: AttachmentRequestConflictMode
  readonly expectedExistingSha256?: string | null
}

export async function attachmentRequestApprove(id: string, approval: AttachmentRequestApproval): Promise<AttachmentRequest> {
  const body: Raw = { decision: 'approve', expected_doc_id: approval.expectedDocId, conflict: approval.conflict ?? 'keep_both' }
  if (approval.conflict === 'overwrite') body.expected_existing_sha256 = approval.expectedExistingSha256 ?? null
  const response = await postRequest<unknown>(`${one(id)}/decision`, body)
  const row = toAttachmentRequest(record(response.data).request)
  if (!row) throw new Error('decision response without request')
  return row
}

export async function attachmentRequestReject(id: string): Promise<AttachmentRequest> {
  const response = await postRequest<unknown>(`${one(id)}/decision`, { decision: 'reject' })
  const row = toAttachmentRequest(record(response.data).request)
  if (!row) throw new Error('decision response without request')
  return row
}

/** One request of a bulk decision: `expectedDocId` (the target the screen shows) is required to approve. */
export interface AttachmentRequestBulkItem {
  readonly id: string
  readonly expectedDocId?: string | null
}

/**
 * done = decided; skipped = left pending for its own row (same name on the target, a changed target) or already decided
 * by someone else; failed = refused (the error kind says why).
 */
export type AttachmentRequestBulkOutcome = 'done' | 'skipped' | 'failed'

export interface AttachmentRequestBulkResult {
  readonly id: string
  readonly outcome: AttachmentRequestBulkOutcome
  readonly request: AttachmentRequest | null
  readonly code: string | null
}

export interface AttachmentRequestBulkSummary {
  readonly decision: 'approve' | 'reject'
  readonly results: readonly AttachmentRequestBulkResult[]
  readonly done: number
  readonly skipped: number
  readonly failed: number
}

/** The server's bulk limit (`requests.BULK_MAX_ITEMS`); larger selections are sent in chunks of this size. */
export const ATTACHMENT_REQUEST_BULK_MAX = 100

/**
 * Approve all / reject all / the selected ones: one call per 100 requests, one result per request (never all-or-nothing).
 * A bulk approval is always keep both; the server skips what needs a person's own look.
 */
export async function attachmentRequestDecideMany(
  decision: 'approve' | 'reject',
  items: readonly AttachmentRequestBulkItem[],
): Promise<AttachmentRequestBulkSummary> {
  const results: AttachmentRequestBulkResult[] = []
  for (let start = 0; start < items.length; start += ATTACHMENT_REQUEST_BULK_MAX) {
    const chunk = items.slice(start, start + ATTACHMENT_REQUEST_BULK_MAX).map((item) =>
      decision === 'approve' ? { request_id: item.id, expected_doc_id: item.expectedDocId ?? null } : { request_id: item.id },
    )
    const response = await postRequest<unknown>(`${base}/decisions`, { decision, items: chunk })
    for (const raw of list(record(response.data).results)) {
      const row = record(raw)
      const id = text(row.request_id)
      if (!id) continue
      const outcome = text(row.outcome)
      results.push({
        id,
        outcome: outcome === 'done' || outcome === 'skipped' ? outcome : 'failed',
        request: toAttachmentRequest(row.request),
        code: text(record(row.error).code),
      })
    }
  }
  const count = (outcome: AttachmentRequestBulkOutcome) => results.filter((r) => r.outcome === outcome).length
  return { decision, results, done: count('done'), skipped: count('skipped'), failed: count('failed') }
}

/** The limits keys (server `request_settings.KEYS`). */
export const ATTACHMENT_REQUEST_LIMIT_KEYS = [
  'max_file_bytes',
  'max_attachments_per_doc',
  'max_pending_per_target',
  'max_requests_per_run',
  'max_pending_bytes_per_project',
  'max_filename_bytes',
  'request_retention_days',
  'backup_retention_days',
] as const
export type AttachmentRequestLimitKey = (typeof ATTACHMENT_REQUEST_LIMIT_KEYS)[number]

export interface AttachmentRequestLimits {
  readonly values: Readonly<Record<AttachmentRequestLimitKey, number>> & { readonly blocked_extensions: readonly string[] }
  readonly sources: Readonly<Record<string, 'project' | 'system' | 'default' | string>>
}

function toLimitValues(raw: unknown): AttachmentRequestLimits['values'] {
  const row = record(raw)
  const out: Record<string, unknown> = {}
  for (const key of ATTACHMENT_REQUEST_LIMIT_KEYS) out[key] = whole(row[key]) ?? 0
  out.blocked_extensions = list(row.blocked_extensions).filter((v): v is string => typeof v === 'string')
  return out as AttachmentRequestLimits['values']
}

function toSources(raw: unknown): Record<string, string> {
  const out: Record<string, string> = {}
  for (const [key, value] of Object.entries(record(raw))) if (typeof value === 'string') out[key] = value
  return out
}

export async function attachmentRequestLimits(projectId: string): Promise<AttachmentRequestLimits> {
  const response = await getRequest<unknown>(`${base}/limits`, { project_id: projectId })
  const data = record(response.data)
  return { values: toLimitValues(data.values), sources: toSources(data.sources) }
}

export interface AttachmentRequestBounds {
  readonly [key: string]: { readonly default?: unknown; readonly min?: number; readonly max?: number; readonly max_entries?: number }
}

export interface AttachmentRequestSystemSettings {
  readonly values: AttachmentRequestLimits['values']
  readonly stored: Readonly<Record<string, unknown>>
  readonly bounds: AttachmentRequestBounds
}

function toBounds(raw: unknown): AttachmentRequestBounds {
  const out: Record<string, Raw> = {}
  for (const [key, value] of Object.entries(record(raw))) out[key] = record(value)
  return out as AttachmentRequestBounds
}

function toSystemSettings(raw: unknown): AttachmentRequestSystemSettings {
  const data = record(raw)
  return { values: toLimitValues(data.values), stored: record(data.stored), bounds: toBounds(data.bounds) }
}

export async function attachmentRequestSystemSettings(): Promise<AttachmentRequestSystemSettings> {
  const response = await getRequest<unknown>(`${API}/settings/attachment-requests`)
  return toSystemSettings(response.data)
}

export async function attachmentRequestSaveSystemSettings(values: Readonly<Record<string, unknown>>): Promise<AttachmentRequestSystemSettings> {
  const response = await putRequest<unknown>(`${API}/settings/attachment-requests`, { values })
  return toSystemSettings(response.data)
}

export interface AttachmentRequestProjectSettings {
  readonly projectId: string
  readonly overrides: Readonly<Record<string, unknown>>
  readonly inherited: AttachmentRequestLimits['values']
  readonly effective: AttachmentRequestLimits['values']
  readonly sources: Readonly<Record<string, string>>
  readonly bounds: AttachmentRequestBounds
}

function toProjectSettings(projectId: string, raw: unknown): AttachmentRequestProjectSettings {
  const data = record(raw)
  return {
    projectId,
    overrides: record(data.overrides),
    inherited: toLimitValues(data.inherited),
    effective: toLimitValues(data.effective),
    sources: toSources(data.sources),
    bounds: toBounds(data.bounds),
  }
}

const projectSettingsUrl = (projectId: string) => `${API}/projects/${encodeURIComponent(projectId)}/attachment-request-settings`

export async function attachmentRequestProjectSettings(projectId: string): Promise<AttachmentRequestProjectSettings> {
  const response = await getRequest<unknown>(projectSettingsUrl(projectId))
  return toProjectSettings(projectId, response.data)
}

/** Saves a project's overrides; a key sent as null (or left out) inherits the system value. */
export async function attachmentRequestSaveProjectSettings(
  projectId: string,
  overrides: Readonly<Record<string, unknown>>,
): Promise<AttachmentRequestProjectSettings> {
  const response = await putRequest<unknown>(projectSettingsUrl(projectId), { overrides })
  return toProjectSettings(projectId, response.data)
}

/**
 * Why a request call was refused. target_changed = the card's document is not the one shown any more (details carry
 * current_doc_id / current_title); already_decided = someone else decided first; existing_changed = the same-name
 * attachment changed since it was shown; overwrite_blocked = an AI run is active (keep both still works);
 * forbidden = no permission; closed = the request (and its staged file) is gone; busy = the existing file is in use.
 */
export type AttachmentRequestErrorKind =
  | 'target_changed'
  | 'target_missing'
  | 'already_decided'
  | 'existing_changed'
  | 'overwrite_blocked'
  | 'case_mismatch'
  | 'doc_full'
  | 'not_mutable'
  | 'forbidden'
  | 'closed'
  | 'busy'
  | 'invalid'
  | 'server'
  | 'network'
  | 'other'

export interface AttachmentRequestError {
  readonly kind: AttachmentRequestErrorKind
  readonly status: number | null
  readonly code: string | null
  readonly currentDocId: string | null
  readonly currentTitle: string | null
}

const KIND_OF_CODE: Readonly<Record<string, AttachmentRequestErrorKind>> = {
  attachment_target_changed: 'target_changed',
  attachment_target_document_missing: 'target_missing',
  attachment_target_unavailable: 'target_missing',
  attachment_request_already_decided: 'already_decided',
  attachment_existing_changed: 'existing_changed',
  attachment_overwrite_blocked_ai_running: 'overwrite_blocked',
  attachment_overwrite_case_mismatch: 'case_mismatch',
  attachment_overwrite_refused: 'case_mismatch',
  attachment_doc_full: 'doc_full',
  attachment_target_not_mutable: 'not_mutable',
  attachment_request_forbidden: 'forbidden',
  attachment_request_closed: 'closed',
  attachment_request_file_missing: 'closed',
  attachment_request_not_found: 'closed',
  attachment_file_busy: 'busy',
}

export function attachmentRequestsErrorOf(error: unknown): AttachmentRequestError {
  const normalized = normalizeApiError(error)
  const { status, code } = normalized
  const details = record(normalized.source.details)
  let kind: AttachmentRequestErrorKind
  if (normalized.network) kind = 'network'
  else if (code && KIND_OF_CODE[code]) kind = KIND_OF_CODE[code]
  else if (status === 403) kind = 'forbidden'
  else if (status === 404 || status === 410) kind = 'closed'
  else if (status === 400 || status === 422) kind = 'invalid'
  else if (status !== null && status >= 500) kind = 'server'
  else kind = 'other'
  return { kind, status, code, currentDocId: text(details.current_doc_id), currentTitle: text(details.current_title) }
}
