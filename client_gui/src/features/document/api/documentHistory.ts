// flowgate.v02.0056 F1 — a document's review history (0056 CH §2): the AI reviews (`ai_review_history`)
// and the rejections with the AI responses to them (`rejection_history`), as the v0.1 document read already
// carries them — the data behind the v0.1 '검수·반려 전체보기' dialog (client/src/main/components/
// QaReviewHistoryDialog.vue). The 0048 D#1 §10.4 reading ("AI review existence only, never a count") is
// withdrawn by 0056 CH: the history window shows every entry and its count.
//
//   read   GET /api/v1/documents/{doc_id}   (perm_document_read) — the same read as documentDetailRead;
//          only the history parts are taken here, snake_case -> camelCase. No new server API.
//
// The v0.1 shapes (client/src/main/types/aiReview.ts AiReview / AiReviewProvider / AiProvenance,
// useFlowGateToken.ts RejectionHistoryItem) are read defensively: an item without a usable id gets one
// from its time, and the provider that really ran is preferred over the one requested.
import { getRequest } from '@shared/api'
import { toDocumentRecord, type DocumentRecord } from './document'

const API = '/api/v1'

type Raw = Record<string, unknown>

const text = (value: unknown): string | null => (typeof value === 'string' && value ? value : null)
const record = (value: unknown): Raw => (value && typeof value === 'object' && !Array.isArray(value) ? (value as Raw) : {})
const list = (value: unknown): unknown[] => (Array.isArray(value) ? value : [])
const whole = (value: unknown): number | null => (typeof value === 'number' && Number.isInteger(value) ? value : null)
/** An id the server sent as a number or a string, as text. */
const idText = (value: unknown): string | null => (typeof value === 'number' && Number.isFinite(value) ? String(value) : text(value))

/** One finding of an AI review. */
export interface DocumentHistoryFinding {
  readonly locus: string | null
  readonly note: string
}

/** One AI review of the document (a `document_reviews` row). */
export interface DocumentHistoryReview {
  readonly id: string
  readonly revisionNo: number | null
  /** 'pass' | 'issues' | 'hold' as sent; null when missing. */
  readonly verdict: string | null
  readonly findingCount: number
  readonly findings: readonly DocumentHistoryFinding[]
  readonly comment: string | null
  /** 'normal' | 'rerun' as sent; null when missing. */
  readonly intent: string | null
  /** The earlier review this one replaced (a rerun), as text. */
  readonly supersededReviewId: string | null
  /** The AI that reviewed: the one that actually ran, else the reviewer name the server kept. */
  readonly reviewerName: string | null
  readonly requestedProviderId: string | null
  readonly actualProviderId: string | null
  /** When it was reviewed (`reviewed_at`, else `created_at`). */
  readonly at: string | null
}

/** One rejection of the document with the AI response recorded against it. */
export interface DocumentHistoryRejection {
  readonly id: string
  readonly reason: string
  readonly rejectedAt: string | null
  /** The user id that rejected (a person's rejection), as sent. */
  readonly rejectedBy: string | null
  /** An automatic rejection from an AI review (`review_id` present). */
  readonly byAi: boolean
  /** The reviewing AI of an automatic rejection, when the server resolved it. */
  readonly rejectionProviderName: string | null
  readonly aiResponse: string | null
  readonly respondedAt: string | null
  /** The AI that wrote the response, when known. */
  readonly responseProviderName: string | null
}

/** The document's review history with its header record (title, Flow, project). */
export interface DocumentHistory {
  readonly docId: string
  readonly record: DocumentRecord
  /** As stored (oldest first). */
  readonly reviews: readonly DocumentHistoryReview[]
  /** As stored (oldest first). */
  readonly rejections: readonly DocumentHistoryRejection[]
}

function toFinding(raw: unknown): DocumentHistoryFinding | null {
  const row = record(raw)
  const note = text(row.note)
  return note === null ? null : { locus: text(row.locus), note }
}

/** The provider name of an AiProvenance (`ai_provider_name`, else the id), or null. */
function provenanceName(raw: unknown): string | null {
  const row = record(raw)
  return text(row.ai_provider_name) ?? text(row.ai_provider_id)
}

/** One `ai_review_history` item; null when it has neither an id nor a time to stand on. */
export function toDocumentHistoryReview(raw: unknown): DocumentHistoryReview | null {
  const row = record(raw)
  const at = text(row.reviewed_at) ?? text(row.created_at)
  const id = idText(row.id) ?? (at ? `at:${at}` : null)
  if (id === null) return null
  const provider = record(row.review_provider)
  const findings = list(row.findings).flatMap((item) => toFinding(item) ?? [])
  return {
    id,
    revisionNo: whole(row.revision_no),
    verdict: text(row.verdict),
    findingCount: whole(row.finding_count) ?? findings.length,
    findings,
    comment: text(row.comment),
    intent: text(row.review_intent),
    supersededReviewId: idText(row.superseded_review_id),
    reviewerName: text(provider.actual_provider_name) ?? text(provider.actual_provider_id) ?? text(row.reviewer_name),
    requestedProviderId: text(provider.requested_provider_id),
    actualProviderId: text(provider.actual_provider_id),
    at,
  }
}

/** One `rejection_history` item; null without a reason. */
export function toDocumentHistoryRejection(raw: unknown): DocumentHistoryRejection | null {
  const row = record(raw)
  const reason = text(row.reason)
  if (reason === null) return null
  const rejectedAt = text(row.rejected_at)
  return {
    id: text(row.rejection_id) ?? (rejectedAt ? `at:${rejectedAt}` : `reason:${reason}`),
    reason,
    rejectedAt,
    rejectedBy: text(row.rejected_by),
    byAi: row.review_id !== null && row.review_id !== undefined,
    rejectionProviderName: provenanceName(row.rejection_provider),
    aiResponse: text(row.ai_response),
    respondedAt: text(row.responded_at),
    responseProviderName: provenanceName(row.response_provider),
  }
}

/** The v0.1 document response -> DocumentHistory. Throws when it carries no doc_id. */
export function toDocumentHistory(raw: unknown): DocumentHistory {
  const documentRecord = toDocumentRecord(raw)
  const row = record(raw)
  return {
    docId: documentRecord.docId,
    record: documentRecord,
    reviews: list(row.ai_review_history).flatMap((item) => toDocumentHistoryReview(item) ?? []),
    rejections: list(row.rejection_history).flatMap((item) => toDocumentHistoryRejection(item) ?? []),
  }
}

/** Reads one document's review history. Transport errors are thrown as they are (see documentErrorOf). */
export async function documentHistoryRead(docId: string): Promise<DocumentHistory> {
  const response = await getRequest<unknown>(`${API}/documents/${encodeURIComponent(docId)}`)
  return toDocumentHistory(response.data)
}
