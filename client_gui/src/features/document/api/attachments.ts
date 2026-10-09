// flowgate.v02.0048 T#3 — skeleton of the attachments document API (D#1 §6.3). Owned by T#8 (attachments), which
// fills this file only: request functions, their types and the snake_case -> camelCase
// normalization, every export name starting with `attachments`. index.ts already re-exports it. The
// v0.1 endpoints it will wrap (NR0003 §2):
//   list     GET    /api/v1/documents/{doc_id}/attachments
//   open     GET    /api/v1/documents/{doc_id}/attachments/{name}   (+ /read)
//   add      POST   /api/v1/documents/{doc_id}/attachments          (multipart)
//   delete   DELETE /api/v1/documents/{doc_id}/attachments/{name}
//
// flowgate.v02.0048 T#8 — the Inspector attachments tab's calls, all existing v0.1 endpoints (P0011,
// `documents/routers/documents.py`; no new server API). Every JSON answer is wrapped in `{ data }`:
//   list     GET    /api/v1/documents/{doc_id}/attachments -> { data: { doc_id, attachments: [{ doc_id,
//            original_filename, filename, size, content_type, content_sha256, path, path_base,
//            uploaded_by, uploaded_at }], count } }. No attachment is 200 and an empty array.
//   open     GET    /api/v1/documents/{doc_id}/attachments/{filename} -> the file (blob). A query: a
//            disposed group or an AI run does not block it.
//   add      POST   /api/v1/documents/{doc_id}/attachments, multipart, one `file` part per file ->
//            201 { data: { doc_id, attachments: [...], count } }. All files or none.
//   delete   DELETE /api/v1/documents/{doc_id}/attachments/{filename} -> { data: { doc_id, filename,
//            file_deleted, metadata_deleted, deleted_at } }
// `filename` is the stored name (unique in the document) the server answers with; it names the file
// for open and delete. `/read` (content as text) and `/copy` (into the source tree) are not wrapped:
// the tab opens a file by downloading it, and copying is the source worker's channel.
//
// Refusals keep the server's shape and are thrown as they are; attachmentsErrorOf reads them. The
// attachment routes answer `{ error: { code, message, details } }` — 409 DOCUMENT_NOT_MUTABLE with
// `details.reason` group_disposed / group_ai_running, 409 ATTACHMENT_EXISTS (200 per document), 413
// ATTACHMENT_TOO_LARGE, 404 DOCUMENT_NOT_FOUND / ATTACHMENT_NOT_FOUND, 400 / 422, 5xx; the group
// mutation policy answers 423 `{ error: { code: 'GROUP_AI_RUN_LOCKED' } }` before the route while an
// AI run owns the group. A download refusal arrives as a blob and is read back as JSON first.
import { normalizeApiError } from '@shared/apiErrors'
import { deleteRequest, downloadBlobRequest, getRequest, postFormRequest } from '@shared/api'

const API = '/api/v1'

type Raw = Record<string, unknown>

const text = (value: unknown): string | null => (typeof value === 'string' && value ? value : null)
const record = (value: unknown): Raw => (value && typeof value === 'object' && !Array.isArray(value) ? (value as Raw) : {})
const list = (value: unknown): unknown[] => (Array.isArray(value) ? value : [])
const whole = (value: unknown): number | null => (typeof value === 'number' && Number.isInteger(value) && value >= 0 ? value : null)

/** Server limits (`documents/attachments/constants.py`); the server decides, these only spare a doomed upload. */
export const ATTACHMENTS_MAX_UPLOAD_BYTES = 20 * 1024 * 1024
export const ATTACHMENTS_MAX_FILES_PER_REQUEST = 20
export const ATTACHMENTS_MAX_COUNT_PER_DOC = 200

/** One attachment of a document. */
export interface AttachmentsItem {
  /** Stored name, unique in the document: what open and delete name. */
  readonly filename: string
  /** The name the file was uploaded with (may repeat across attachments). */
  readonly originalFilename: string | null
  readonly size: number
  readonly contentType: string | null
  readonly sha256: string | null
  readonly uploadedBy: string | null
  readonly uploadedAt: string | null
}

/** The attachments of one document, in the server's order. */
export interface AttachmentsList {
  readonly docId: string
  readonly items: readonly AttachmentsItem[]
}

const body = (raw: unknown): Raw => {
  const row = record(raw)
  return 'data' in row ? record(row.data) : row
}

/** One attachment object; null without a stored name (it could be neither opened nor deleted). */
function toAttachmentsItem(raw: unknown): AttachmentsItem | null {
  const row = record(raw)
  const filename = text(row.filename)
  if (filename === null) return null
  return {
    filename,
    originalFilename: text(row.original_filename),
    size: whole(row.size) ?? 0,
    contentType: text(row.content_type),
    sha256: text(row.content_sha256),
    uploadedBy: text(row.uploaded_by),
    uploadedAt: text(row.uploaded_at),
  }
}

const itemsOf = (raw: Raw): AttachmentsItem[] => list(raw.attachments).flatMap((item) => toAttachmentsItem(item) ?? [])

/** The list response -> AttachmentsList. Throws when it carries no doc_id. */
export function toAttachmentsList(raw: unknown): AttachmentsList {
  const data = body(raw)
  const docId = text(data.doc_id)
  if (docId === null) throw new Error('attachments response without doc_id')
  return { docId, items: itemsOf(data) }
}

const base = (docId: string) => `${API}/documents/${encodeURIComponent(docId)}/attachments`
const one = (docId: string, filename: string) => `${base(docId)}/${encodeURIComponent(filename)}`

/** Reads the attachments of a document. Transport errors are thrown as they are. */
export async function attachmentsRead(docId: string): Promise<AttachmentsList> {
  const response = await getRequest<unknown>(base(docId))
  return toAttachmentsList(response.data)
}

/** What an upload stored. */
export interface AttachmentsUploadResult {
  readonly docId: string | null
  readonly items: readonly AttachmentsItem[]
}

export function toAttachmentsUploadResult(raw: unknown): AttachmentsUploadResult {
  const data = body(raw)
  return { docId: text(data.doc_id), items: itemsOf(data) }
}

/** Uploads files to a document in one request (one `file` part each); the server stores all or none. */
export async function attachmentsUpload(docId: string, files: readonly File[]): Promise<AttachmentsUploadResult> {
  const form = new FormData()
  for (const file of files) form.append('file', file)
  const response = await postFormRequest<unknown>(base(docId), form)
  return toAttachmentsUploadResult(response.data)
}

export interface AttachmentsDeleteResult {
  readonly docId: string | null
  readonly filename: string | null
  readonly deletedAt: string | null
}

export function toAttachmentsDeleteResult(raw: unknown): AttachmentsDeleteResult {
  const data = body(raw)
  return { docId: text(data.doc_id), filename: text(data.filename), deletedAt: text(data.deleted_at) }
}

/** Deletes one attachment by its stored name. */
export async function attachmentsDelete(docId: string, filename: string): Promise<AttachmentsDeleteResult> {
  const response = await deleteRequest<unknown>(one(docId, filename))
  return toAttachmentsDeleteResult(response.data)
}

/** A downloaded attachment: the file and the name to save it under. */
export interface AttachmentsFile {
  readonly filename: string
  readonly blob: Blob
}

/**
 * Downloads one attachment. The request needs the bearer header, so the file comes as a blob; a
 * refusal also arrives as a blob and is read back into the JSON envelope before it is thrown.
 */
export async function attachmentsDownload(docId: string, filename: string): Promise<AttachmentsFile> {
  try {
    const response = await downloadBlobRequest(one(docId, filename))
    return { filename, blob: response.data }
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
    response.data = JSON.parse(await blobText(data))
  } catch {
    // Not JSON: the status alone is read.
  }
}

function blobText(blob: Blob): Promise<string> {
  if (typeof blob.text === 'function') return blob.text()
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result ?? ''))
    reader.onerror = () => reject(reader.error)
    reader.readAsText(blob)
  })
}

/**
 * Why an attachments call was refused:
 * not_mutable = 409 DOCUMENT_NOT_MUTABLE (the document's group state refuses changes; `reason` says
 * group_disposed or group_ai_running when the server sent it); running = 423 GROUP_AI_RUN_LOCKED (an AI
 * run owns the group); limit = 409 ATTACHMENT_EXISTS (the document holds the maximum); too_large =
 * 413; not_found = 404 (the document or the attachment is gone); forbidden 403; invalid 400 / 422;
 * server = 5xx; network = no answer.
 */
export type AttachmentsErrorKind =
  | 'not_mutable'
  | 'running'
  | 'limit'
  | 'too_large'
  | 'not_found'
  | 'forbidden'
  | 'invalid'
  | 'server'
  | 'network'
  | 'other'

export interface AttachmentsError {
  readonly kind: AttachmentsErrorKind
  readonly status: number | null
  readonly code: string | null
  /** `details.reason` of the envelope (e.g. group_disposed, group_ai_running), when sent. */
  readonly reason: string | null
}

/** What a failed attachments call threw, as kind / status / code / reason. No server message is read. */
export function attachmentsErrorOf(error: unknown): AttachmentsError {
  const normalized = normalizeApiError(error)
  const { status, code } = normalized
  const reason = text(record(normalized.source.details).reason)
  let kind: AttachmentsErrorKind
  if (normalized.network) kind = 'network'
  else if (status === 423 || code === 'GROUP_AI_RUN_LOCKED') kind = 'running'
  else if (code === 'DOCUMENT_NOT_MUTABLE') kind = 'not_mutable'
  else if (code === 'ATTACHMENT_EXISTS') kind = 'limit'
  else if (status === 413 || code === 'ATTACHMENT_TOO_LARGE') kind = 'too_large'
  else if (status === 404) kind = 'not_found'
  else if (status === 403) kind = 'forbidden'
  else if (status === 400 || status === 422) kind = 'invalid'
  else if (status !== null && status >= 500) kind = 'server'
  else kind = 'other'
  return { kind, status, code, reason }
}
