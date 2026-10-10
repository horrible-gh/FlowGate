// flowgate.v02.0048 T#5 — the documents tab's display rules (D#1 §10.2, §8). Pure functions: the
// selected Node's target plus what the tab read (the document with its review extras, and the
// documents it superseded) -> what the tab shows. Only what the existing v0.1 API gives is shown:
// the current document (type · seq · title · review status · rejection reason · AI review
// existence), its rejections, the documents it superseded, the v0.1 sequence slot and the Graph
// node run. No pair slot, result slot or "write" line (D#1 §8.4): writing and editing happen in
// the v0.1 screen through ↗ 열기.
//
// The tab has query operations only (↗ 열기 of a document), so read only Flows (ownership
// conflict, Graph revision view) change nothing here (D#1 §8.3 rule 0).
import type { InspectorTarget } from '../../inspectorTarget'

export type DocumentsTone = 'neutral' | 'info' | 'success' | 'warning' | 'danger'

/** The document fields the tab reads (the `@/features/document` record / detail shape). */
export interface DocumentsRecordInput {
  readonly docId: string
  readonly typeCode: string | null
  readonly seq: number | null
  readonly title: string
  readonly status: string | null
  readonly docReviewStatus: string | null
  readonly revisionNo: number | null
  readonly rejectionReason: string | null
}

export interface DocumentsRejectionInput {
  readonly rejectionId: string | null
  readonly reason: string
  readonly rejectedAt: string | null
  readonly aiResponse: string | null
}

export interface DocumentsDetailInput extends DocumentsRecordInput {
  /** Oldest first, as stored. */
  readonly rejectionHistory: readonly DocumentsRejectionInput[]
  readonly hasAiReview: boolean
}

/** A superseded document as read: its record, or null when the read failed. */
export interface DocumentsRelationInput {
  readonly docId: string
  readonly record: DocumentsRecordInput | null
}

export interface DocumentsCurrentView {
  readonly docId: string
  readonly typeCode: string | null
  readonly seq: number | null
  readonly title: string
  /** The read document's review status, else the Canvas one. */
  readonly reviewStatus: string | null
  readonly reviewTone: DocumentsTone
  readonly status: string | null
  readonly revisionNo: number | null
  /** null until the document is read: the Canvas does not say. */
  readonly hasAiReview: boolean | null
  /** Shown while the document is rejected only. */
  readonly rejectionReason: string | null
}

export interface DocumentsRow {
  /** Message key under `slot.` / `run.`. */
  readonly key: string
  readonly value: string
}

export interface DocumentsSlotView {
  readonly rows: readonly DocumentsRow[]
  readonly protected: boolean
}

export interface DocumentsRunView {
  readonly rows: readonly DocumentsRow[]
  /** The run's result document when it is not the bound document itself (to open it). */
  readonly resultDocId: string | null
}

export interface DocumentsRejectionView {
  readonly key: string
  readonly reason: string
  readonly rejectedAt: string | null
  readonly aiResponse: string | null
}

/** pending: not read yet; ready: read; missing: the read failed (deleted, no permission …). */
export type DocumentsPreviousState = 'pending' | 'ready' | 'missing'

export interface DocumentsPreviousView {
  readonly docId: string
  readonly state: DocumentsPreviousState
  readonly typeCode: string | null
  readonly seq: number | null
  readonly title: string | null
  readonly reviewStatus: string | null
  readonly reviewTone: DocumentsTone
}

export interface DocumentsView {
  readonly current: DocumentsCurrentView
  readonly slot: DocumentsSlotView | null
  readonly run: DocumentsRunView | null
  /** Newest first. */
  readonly rejections: readonly DocumentsRejectionView[]
  /** Newest first. */
  readonly previous: readonly DocumentsPreviousView[]
}

const TONE_BY_REVIEW_STATUS: Readonly<Record<string, DocumentsTone>> = {
  pending_review: 'warning',
  approved: 'success',
  rejected: 'danger',
  revised: 'info',
}

/** Chip tone of a review status; an unknown or missing one is neutral. */
export function documentsReviewTone(status: string | null | undefined): DocumentsTone {
  return (status && TONE_BY_REVIEW_STATUS[status]) || 'neutral'
}

const ISO_MINUTE = /^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})/

/** A server timestamp as `YYYY-MM-DD HH:mm` (its own offset, as sent); other text stays as sent. */
export function documentsTime(value: string | null | undefined): string | null {
  if (!value) return null
  const match = ISO_MINUTE.exec(value)
  return match ? `${match[1]} ${match[2]}` : value
}

const filled = (value: string | null | undefined): string | null => {
  const trimmed = typeof value === 'string' ? value.trim() : ''
  return trimmed ? trimmed : null
}

const row = (key: string, value: string | number | null | undefined): DocumentsRow[] =>
  value === null || value === undefined || value === '' ? [] : [{ key, value: String(value) }]

/** The v0.1 sequence slot rows (v0.1 Flows only). */
export function documentsSlot(slot: InspectorTarget['slot']): DocumentsSlotView | null {
  if (!slot) return null
  return {
    rows: [
      ...row('order', slot.order),
      ...row('type', filled(slot.type)),
      ...row('label', filled(slot.label)),
      ...row('derived_status', filled(slot.derivedStatus)),
      ...row('wp_card', filled(slot.sourceWpCardId)),
    ],
    protected: slot.protected === true,
  }
}

/** The Graph node run rows (Graph Flows only); null without a run. */
export function documentsRun(run: InspectorTarget['nodeRun'], docId: string | null): DocumentsRunView | null {
  if (!run) return null
  const resultDocId = filled(run.resultDocId)
  const rows = [
    ...row('status', filled(run.status)),
    ...row('opened_at', documentsTime(run.openedAt)),
    ...row('finished_at', documentsTime(run.finishedAt)),
    ...row('result_doc', resultDocId),
  ]
  if (!rows.length) return null
  return { rows, resultDocId: resultDocId !== null && resultDocId !== docId ? resultDocId : null }
}

/** Rejections newest first; an item without a reason is dropped. */
export function documentsRejections(history: readonly DocumentsRejectionInput[] | null | undefined): DocumentsRejectionView[] {
  return (history ?? [])
    .map((item, index) => ({ item, index }))
    .filter(({ item }) => filled(item.reason) !== null)
    .reverse()
    .map(({ item, index }) => ({
      key: item.rejectionId ?? `#${index}`,
      reason: item.reason.trim(),
      rejectedAt: documentsTime(item.rejectedAt),
      aiResponse: filled(item.aiResponse),
    }))
}

/**
 * The superseded documents, newest first. Before they are read the Canvas ids are listed
 * (`pending`); a document whose read failed stays listed by id (`missing`).
 */
export function documentsPrevious(
  supersedes: readonly string[],
  relations: readonly DocumentsRelationInput[] | null,
): DocumentsPreviousView[] {
  const ids = relations ? relations.map((relation) => relation.docId) : [...new Set(supersedes)]
  return ids.reverse().map((docId) => {
    const record = relations?.find((candidate) => candidate.docId === docId)?.record ?? null
    const state: DocumentsPreviousState = !relations ? 'pending' : record ? 'ready' : 'missing'
    return {
      docId,
      state,
      typeCode: record?.typeCode ?? null,
      seq: record?.seq ?? null,
      title: filled(record?.title),
      reviewStatus: record?.docReviewStatus ?? null,
      reviewTone: documentsReviewTone(record?.docReviewStatus),
    }
  })
}

/**
 * What the tab shows for a target. Null without a bound document (the tab is not registered for
 * such a Node; this only guards). Before the read answers, the Canvas values are shown.
 */
export function documentsView(
  target: InspectorTarget,
  detail: DocumentsDetailInput | null,
  relations: readonly DocumentsRelationInput[] | null,
): DocumentsView | null {
  if (!target.docId) return null
  const own = detail && detail.docId === target.docId ? detail : null
  const reviewStatus = own ? own.docReviewStatus : target.docReviewStatus
  const history = own?.rejectionHistory ?? []
  const lastReason = history.length ? filled(history[history.length - 1].reason) : null
  return {
    current: {
      docId: target.docId,
      typeCode: own?.typeCode ?? target.docType,
      seq: own ? own.seq : null,
      title: filled(own?.title) ?? target.title,
      reviewStatus,
      reviewTone: documentsReviewTone(reviewStatus),
      status: own ? own.status : target.docStatus,
      revisionNo: own ? own.revisionNo : null,
      hasAiReview: own ? own.hasAiReview : null,
      rejectionReason: reviewStatus === 'rejected' ? (filled(own?.rejectionReason) ?? lastReason) : null,
    },
    slot: documentsSlot(target.slot),
    run: documentsRun(target.nodeRun, target.docId),
    rejections: documentsRejections(own?.rejectionHistory),
    previous: documentsPrevious(target.supersedes, own ? relations : null),
  }
}

export interface DocumentsAvailability {
  readonly visible: boolean
  readonly enabled: boolean
  /** A key of this feature's messages; null when enabled or hidden. */
  readonly reason: string | null
}

export type DocumentsOperation = 'open'

/** Operation kinds (D#1 §8.3 rule 0): the tab only views. */
export const DOCUMENTS_OPERATION_KINDS: Readonly<Record<DocumentsOperation, 'query' | 'change'>> = { open: 'query' }

export type DocumentsAvailabilityInput = Pick<InspectorTarget, 'docId' | 'readOnly' | 'ownership' | 'viewMode' | 'cardKind'>

/**
 * Availability of the tab's operations. ↗ 열기 is a query: shown and enabled whenever there is a
 * document, whatever the ownership or the read only reason.
 */
export function documentsAvailability(
  input: DocumentsAvailabilityInput | null,
): Readonly<Record<DocumentsOperation, DocumentsAvailability>> {
  const visible = !!input?.docId
  return { open: { visible, enabled: visible, reason: null } }
}

/** Message key of a failed read, by the document error kind (documentErrorOf). */
export function documentsErrorKey(kind: string | null | undefined): string {
  return kind === 'not_found' || kind === 'forbidden' || kind === 'network' ? `error.${kind}` : 'error.other'
}
