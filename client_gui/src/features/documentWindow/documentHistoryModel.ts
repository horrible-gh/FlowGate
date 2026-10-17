// flowgate.v02.0056 F1 — what the review history window decides without a screen or a server call (0056
// CH §2): the one timeline of a document's AI reviews and rejections (newest first, as the v0.1
// QaReviewHistoryDialog ordered them; flowgate.v02.0056 F2-2: whatever order the read sends), the filter and
// its counts, and the short display words. Pure.
import type { DocumentHistory, DocumentHistoryRejection, DocumentHistoryReview } from '@/features/document'

export type DocumentHistoryFilter = 'all' | 'reject' | 'ai_review'

export const DOCUMENT_HISTORY_FILTERS: readonly DocumentHistoryFilter[] = ['all', 'reject', 'ai_review']

export interface DocumentHistoryRejectEntry {
  readonly kind: 'reject'
  readonly key: string
  readonly at: string
  readonly rejection: DocumentHistoryRejection
}

export interface DocumentHistoryReviewEntry {
  readonly kind: 'ai_review'
  readonly key: string
  readonly at: string
  readonly review: DocumentHistoryReview
}

export type DocumentHistoryEntry = DocumentHistoryRejectEntry | DocumentHistoryReviewEntry

/**
 * Rejections and AI reviews as one list, newest first; entries without a time go last.
 * flowgate.v02.0056 F2-2 — the order no longer leans on what the read happens to send: the server sends the AI
 * reviews newest first and the rejections oldest first (O3 0009-TR §4-4; F1 took both as "oldest first").
 * Times are compared as instants (a `Z` and a `+09:00` time of the same moment are the same time). Entries of the
 * same moment, and entries without a time, are newest first within their kind (the reviews as sent, the
 * rejections in reverse), the reviews before the rejections.
 */
export function documentHistoryTimeline(history: Pick<DocumentHistory, 'reviews' | 'rejections'> | null): DocumentHistoryEntry[] {
  if (!history) return []
  const entries: DocumentHistoryEntry[] = [
    ...history.reviews.map((review): DocumentHistoryReviewEntry => ({ kind: 'ai_review', key: `ai_review:${review.id}`, at: review.at ?? '', review })),
    ...[...history.rejections]
      .reverse()
      .map((rejection): DocumentHistoryRejectEntry => ({ kind: 'reject', key: `reject:${rejection.id}`, at: rejection.rejectedAt ?? '', rejection })),
  ]
  // A stable sort: equal times keep the order above.
  return entries
    .map((entry, index) => ({ entry, index }))
    .sort((a, b) => {
      if (a.entry.at === b.entry.at) return a.index - b.index
      if (!a.entry.at) return 1
      if (!b.entry.at) return -1
      const first = Date.parse(a.entry.at)
      const second = Date.parse(b.entry.at)
      if (!Number.isNaN(first) && !Number.isNaN(second)) return first === second ? a.index - b.index : second - first
      return b.entry.at.localeCompare(a.entry.at)
    })
    .map(({ entry }) => entry)
}

export type DocumentHistoryCounts = Readonly<Record<DocumentHistoryFilter, number>>

export function documentHistoryCounts(entries: readonly DocumentHistoryEntry[]): DocumentHistoryCounts {
  return {
    all: entries.length,
    reject: entries.filter((entry) => entry.kind === 'reject').length,
    ai_review: entries.filter((entry) => entry.kind === 'ai_review').length,
  }
}

export function documentHistoryFiltered(entries: readonly DocumentHistoryEntry[], filter: DocumentHistoryFilter): DocumentHistoryEntry[] {
  if (filter === 'all') return [...entries]
  return entries.filter((entry) => entry.kind === filter)
}

/** The verdict word of a review (message key under `gui.document_window.history.verdict`): pass / hold / issues. */
export function documentHistoryVerdictKey(verdict: string | null): 'pass' | 'hold' | 'issues' {
  if (verdict === 'pass') return 'pass'
  if (verdict === 'hold') return 'hold'
  return 'issues'
}

const ISO_MINUTE = /^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})/

/** A server timestamp as `YYYY-MM-DD HH:mm` in the viewer's local time; other text stays as sent. */
export function documentHistoryWhen(value: string | null | undefined): string {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) {
    const match = ISO_MINUTE.exec(value)
    return match ? `${match[1]} ${match[2]}` : value
  }
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`
}

/**
 * What a rerun review replaced, for its note: the earlier review's time when it is in the history, else
 * its id. Null when the review replaced nothing.
 */
export function documentHistorySupersedes(review: DocumentHistoryReview, reviews: readonly DocumentHistoryReview[]): string | null {
  if (review.supersededReviewId === null) return null
  const previous = reviews.find((candidate) => candidate.id === review.supersededReviewId)
  const when = previous ? documentHistoryWhen(previous.at) : ''
  return when || `#${review.supersededReviewId}`
}
