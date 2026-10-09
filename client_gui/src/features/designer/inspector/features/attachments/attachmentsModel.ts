// flowgate.v02.0048 T#8 — the attachments tab's rules (D#1 §8, §10.2 T#8 row, §10.3, §10.4). Pure
// functions over the target and the attachments of its document (`GET /documents/{doc_id}/attachments`,
// normalized by `@/features/document`): which document the attachments belong to, the rows shown, the
// tab number, and whether adding and deleting are possible now.
//
// Target document (D#1 §10.3): attachments belong to one document, and a Canvas Node carries one live
// document. An instruction and its report are separate Nodes, so the target document is always the
// selected Node's own `docId` — an instruction Node shows the instruction's attachments, its report Node
// the report's. A Node without a document has none (the tab is not registered for it, D#1 §4.4).
//
// Operations (D#1 §8.3 rule 0): listing and opening (download) are queries, always possible; adding and
// deleting are changes, disabled with one reason — ownership conflict, revision view, disposed group,
// AI running, a refusal of the server's mutability guard (409 DOCUMENT_NOT_MUTABLE) that no fact read
// explains, then the per-document maximum (adding only), then a change in flight.
// Not offered (D#1 §8.4): the per-file AI hand-off mark.
import type { InspectorCardKind, InspectorReadOnly } from '../../inspectorTarget'

/** The parts of the target the document rule reads. */
export interface AttachmentsTargetFacts {
  readonly docId: string | null
  readonly docType: string | null
  readonly cardKind: InspectorCardKind
}

/**
 * The document whose attachments the tab shows: the selected Node's own document, for every card kind
 * (no instruction / report pair model in 0048 — each is its own Node). Null without a document.
 */
export function attachmentsTargetDocId(target: AttachmentsTargetFacts | null): string | null {
  return target?.docId ?? null
}

/** The parts of an attachment the rules read (the `@/features/document` AttachmentsItem). */
export interface AttachmentsItemFacts {
  readonly filename: string
  readonly originalFilename: string | null
  readonly size: number
  readonly uploadedAt: string | null
}

export interface AttachmentsSize {
  /** Message key (under `gui.inspector.attachments.`) of the unit. */
  readonly key: 'size.bytes' | 'size.kilobytes' | 'size.megabytes'
  readonly value: string
}

/** File size as a unit key and a value: bytes below 1 KiB, then KiB, then MiB with one decimal. */
export function attachmentsSize(bytes: number): AttachmentsSize {
  const size = Number.isFinite(bytes) && bytes > 0 ? Math.floor(bytes) : 0
  if (size < 1024) return { key: 'size.bytes', value: String(size) }
  if (size < 1024 * 1024) return { key: 'size.kilobytes', value: (size / 1024).toFixed(1) }
  return { key: 'size.megabytes', value: (size / (1024 * 1024)).toFixed(1) }
}

export interface AttachmentsRowView {
  /** Stored name: the row identity, what open and delete name, and the name shown. */
  readonly filename: string
  /** The upload name when it differs from the stored one (the server numbered a repeated name). */
  readonly originalFilename: string | null
  readonly size: AttachmentsSize
  readonly uploadedAt: string | null
}

/** The tab's rows: the server's order, one row per stored name. */
export function attachmentsView(items: readonly AttachmentsItemFacts[]): AttachmentsRowView[] {
  const seen = new Set<string>()
  return items.flatMap((item) => {
    if (!item.filename || seen.has(item.filename)) return []
    seen.add(item.filename)
    const original = item.originalFilename && item.originalFilename !== item.filename ? item.originalFilename : null
    return [{ filename: item.filename, originalFilename: original, size: attachmentsSize(item.size), uploadedAt: item.uploadedAt }]
  })
}

/** The attachments tab number (D#1 §10.4): the list length when at least one; none otherwise (or not read). */
export function attachmentsTabCount(items: readonly unknown[] | null): number | null {
  return items !== null && items.length > 0 ? items.length : null
}

export type AttachmentsOperation = 'read' | 'open' | 'add' | 'delete'

/** Listing and opening are queries; adding and deleting change the server state (D#1 §8.3 rule 0). */
export const ATTACHMENTS_OPERATION_CLASS: Readonly<Record<AttachmentsOperation, 'change' | 'query'>> = {
  read: 'query',
  open: 'query',
  add: 'change',
  delete: 'change',
}

/** What the server's mutability guard last refused with on this document, if not cleared since. */
export type AttachmentsBlocked = 'group_disposed' | 'group_ai_running' | 'not_mutable' | null

export interface AttachmentsInput {
  readonly readOnly: InspectorReadOnly
  readonly groupDisposed: boolean
  /** An AI run owns the Flow's group. */
  readonly aiRunning: boolean
  /** The last add / delete was refused by the mutability guard (409 DOCUMENT_NOT_MUTABLE, 423). */
  readonly blocked: AttachmentsBlocked
  /** Attachments of the document, null before the list is read. */
  readonly count: number | null
  /** The per-document maximum (server value). */
  readonly maxCount: number
  /** An add or delete is waiting for the server or for the read after it. */
  readonly busy: boolean
}

export interface AttachmentsAvailabilityItem {
  readonly visible: boolean
  readonly enabled: boolean
  /** Message key (under `gui.inspector.attachments.`) of why it is disabled, or null. */
  readonly reason: string | null
}

export type AttachmentsAvailability = Readonly<Record<AttachmentsOperation, AttachmentsAvailabilityItem>>

/** Why a change is disabled: one reason, in the order of D#1 §8.3 rule 6-1. */
export function attachmentsDisabledReason(input: AttachmentsInput, operation: 'add' | 'delete'): string | null {
  if (input.readOnly === 'conflict') return 'reason.read_only_conflict'
  if (input.readOnly === 'revision') return 'reason.read_only_revision'
  if (input.groupDisposed || input.blocked === 'group_disposed') return 'reason.disposed'
  if (input.aiRunning || input.blocked === 'group_ai_running') return 'reason.ai_running'
  if (input.blocked === 'not_mutable') return 'reason.not_mutable'
  if (operation === 'add' && input.count !== null && input.count >= input.maxCount) return 'reason.limit'
  if (input.busy) return 'reason.busy'
  return null
}

/**
 * Listing and opening are always possible; adding and deleting are offered in every ownership (a
 * document feature, D#1 §8.3 rules 2-4) and disabled with the first reason that holds.
 */
export function attachmentsAvailability(input: AttachmentsInput): AttachmentsAvailability {
  const add = attachmentsDisabledReason(input, 'add')
  const remove = attachmentsDisabledReason(input, 'delete')
  return {
    read: { visible: true, enabled: true, reason: null },
    open: { visible: true, enabled: true, reason: null },
    add: { visible: true, enabled: add === null, reason: add },
    delete: { visible: true, enabled: remove === null, reason: remove },
  }
}

/** The limits an upload is checked against before it is sent (server values). */
export interface AttachmentsUploadLimits {
  readonly maxBytes: number
  readonly maxFiles: number
  readonly maxCount: number
}

/** Why a picked set of files is not sent, as a message key and its parameters; null when it may go. */
export interface AttachmentsUploadRefusal {
  readonly key: string
  readonly params: Readonly<Record<string, string | number>>
}

/**
 * Checks picked files against the server limits so a doomed upload is not sent (the server checks
 * again and stores all or none): at least one file, at most the files of one request, no file over
 * the size limit, and room in the document.
 */
export function attachmentsUploadRefusal(
  files: readonly { readonly name: string; readonly size: number }[],
  count: number | null,
  limits: AttachmentsUploadLimits,
): AttachmentsUploadRefusal | null {
  if (files.length === 0) return { key: 'refusal.no_file', params: {} }
  if (files.length > limits.maxFiles) return { key: 'refusal.too_many_files', params: { limit: limits.maxFiles } }
  const large = files.find((file) => file.size > limits.maxBytes)
  if (large) return { key: 'refusal.too_large', params: { name: large.name, limit: Math.floor(limits.maxBytes / (1024 * 1024)) } }
  if (count !== null && count + files.length > limits.maxCount) return { key: 'refusal.limit', params: { limit: limits.maxCount } }
  return null
}

/** Kinds of a refused call or a failed read (the `@/features/document` attachmentsErrorOf kinds). */
export type AttachmentsFailureKind =
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

/**
 * What a mutability refusal blocks until it is cleared: 409 DOCUMENT_NOT_MUTABLE (by its reason) or
 * 423 (an AI run owns the group). Other refusals block nothing.
 */
export function attachmentsBlockedBy(kind: AttachmentsFailureKind, reason: string | null): AttachmentsBlocked {
  if (kind === 'running') return 'group_ai_running'
  if (kind !== 'not_mutable') return null
  if (reason === 'group_disposed' || reason === 'group_ai_running') return reason
  return 'not_mutable'
}

/** Message key of a refused add / delete / open; the list is read again after a change. */
export function attachmentsFailureKey(operation: 'add' | 'delete' | 'open', kind: AttachmentsFailureKind): string {
  if (kind === 'not_mutable' || kind === 'running') return `failure.${kind}`
  if (kind === 'limit' || kind === 'too_large' || kind === 'invalid') return operation === 'add' ? `failure.add_${kind}` : `failure.${operation}_other`
  if (kind === 'not_found' || kind === 'forbidden' || kind === 'network' || kind === 'server') return `failure.${kind}`
  return `failure.${operation}_other`
}

/** Message key of a failed read of the list. */
export function attachmentsErrorKey(kind: AttachmentsFailureKind): string {
  return kind === 'forbidden' || kind === 'not_found' || kind === 'network' ? `error.${kind}` : 'error.other'
}
