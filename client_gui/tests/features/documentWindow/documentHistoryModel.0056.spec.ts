/**
 * flowgate.v02.0056 F1 — the review history window's rules (documentWindow/documentHistoryModel.ts): one
 * timeline of a document's rejections and AI reviews newest first (ties keep the stored order, entries
 * without a time go last), the 전체 / 반려 / AI 검수 filter with its counts, the verdict word, the short time
 * and the note of what a rerun replaced. Pure.
 */
import { describe, expect, it } from 'vitest'
import type { DocumentHistoryRejection, DocumentHistoryReview } from '@/features/document'
import {
  DOCUMENT_HISTORY_FILTERS,
  documentHistoryCounts,
  documentHistoryFiltered,
  documentHistorySupersedes,
  documentHistoryTimeline,
  documentHistoryVerdictKey,
  documentHistoryWhen,
} from '@/features/documentWindow/documentHistoryModel'

const review = (id: string, at: string | null, fields: Partial<DocumentHistoryReview> = {}): DocumentHistoryReview => ({
  id,
  revisionNo: null,
  verdict: 'pass',
  findingCount: 0,
  findings: [],
  comment: null,
  intent: null,
  supersededReviewId: null,
  reviewerName: null,
  requestedProviderId: null,
  actualProviderId: null,
  at,
  ...fields,
})

const rejection = (id: string, rejectedAt: string | null, fields: Partial<DocumentHistoryRejection> = {}): DocumentHistoryRejection => ({
  id,
  reason: '사유',
  rejectedAt,
  rejectedBy: null,
  byAi: false,
  rejectionProviderName: null,
  aiResponse: null,
  respondedAt: null,
  responseProviderName: null,
  ...fields,
})

const HISTORY = {
  reviews: [review('a', '2026-10-09T15:00:00+09:00'), review('b', '2026-10-09T17:00:00+09:00'), review('c', null)],
  rejections: [
    rejection('r1', '2026-10-09T15:05:00+09:00'),
    rejection('r2', '2026-10-09T18:00:00+09:00'),
    rejection('r3', null),
    rejection('r4', '2026-10-09T15:00:00+09:00'),
  ],
}

const keys = (entries: ReturnType<typeof documentHistoryTimeline>) => entries.map((entry) => entry.key)

describe('documentHistoryTimeline (flowgate.v02.0056 F1)', () => {
  it('rejections and reviews as one list newest first; the same instant keeps the stored order (reviews first); no time goes last', () => {
    expect(keys(documentHistoryTimeline(HISTORY))).toEqual([
      'reject:r2',
      'ai_review:b',
      'reject:r1',
      'ai_review:a',
      'reject:r4',
      'ai_review:c',
      'reject:r3',
    ])
  })

  it('an entry carries its kind, its time and its item', () => {
    const entries = documentHistoryTimeline(HISTORY)
    expect(entries[0]).toEqual({ kind: 'reject', key: 'reject:r2', at: '2026-10-09T18:00:00+09:00', rejection: HISTORY.rejections[1] })
    expect(entries[1]).toEqual({ kind: 'ai_review', key: 'ai_review:b', at: '2026-10-09T17:00:00+09:00', review: HISTORY.reviews[1] })
    expect(entries[entries.length - 1]).toMatchObject({ kind: 'reject', at: '', rejection: HISTORY.rejections[2] })
  })

  it('no history: no entries', () => {
    expect(documentHistoryTimeline(null)).toEqual([])
    expect(documentHistoryTimeline({ reviews: [], rejections: [] })).toEqual([])
  })
})

describe('filter and counts (flowgate.v02.0056 F1)', () => {
  const entries = documentHistoryTimeline(HISTORY)

  it('the filters are 전체 · 반려 · AI 검수 in that order', () => {
    expect(DOCUMENT_HISTORY_FILTERS).toEqual(['all', 'reject', 'ai_review'])
  })

  it('counts every kind', () => {
    expect(documentHistoryCounts(entries)).toEqual({ all: 7, reject: 4, ai_review: 3 })
    expect(documentHistoryCounts([])).toEqual({ all: 0, reject: 0, ai_review: 0 })
  })

  it('filters by kind and keeps the order; 전체 is a copy', () => {
    expect(keys(documentHistoryFiltered(entries, 'reject'))).toEqual(['reject:r2', 'reject:r1', 'reject:r4', 'reject:r3'])
    expect(keys(documentHistoryFiltered(entries, 'ai_review'))).toEqual(['ai_review:b', 'ai_review:a', 'ai_review:c'])
    const all = documentHistoryFiltered(entries, 'all')
    expect(all).toEqual(entries)
    expect(all).not.toBe(entries)
  })
})

describe('display words (flowgate.v02.0056 F1)', () => {
  it('the verdict word: pass and hold as sent, everything else (issues, reject, unknown, missing) is issues', () => {
    expect(documentHistoryVerdictKey('pass')).toBe('pass')
    expect(documentHistoryVerdictKey('hold')).toBe('hold')
    expect(documentHistoryVerdictKey('issues')).toBe('issues')
    expect(documentHistoryVerdictKey('reject')).toBe('issues')
    expect(documentHistoryVerdictKey(null)).toBe('issues')
  })

  it('the time: a server timestamp as YYYY-MM-DD HH:mm in local time; an unparseable ISO-like text to the minute; other text as sent; nothing for none', () => {
    const value = '2026-10-09T15:05:30+09:00'
    const date = new Date(value)
    const pad = (n: number) => String(n).padStart(2, '0')
    expect(documentHistoryWhen(value)).toBe(
      `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`,
    )
    expect(documentHistoryWhen(value)).toMatch(/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$/)
    expect(documentHistoryWhen('2026-13-45T99:99:00')).toBe('2026-13-45 99:99')
    expect(documentHistoryWhen('어제')).toBe('어제')
    expect(documentHistoryWhen('')).toBe('')
    expect(documentHistoryWhen(null)).toBe('')
    expect(documentHistoryWhen(undefined)).toBe('')
  })

  it('what a rerun replaced: the time of the earlier review when it is in the history, else its id; nothing when it replaced nothing', () => {
    const earlier = review('7', '2026-10-09T15:00:00+09:00')
    const rerun = review('8', '2026-10-09T17:00:00+09:00', { intent: 'rerun', supersededReviewId: '7' })
    expect(documentHistorySupersedes(rerun, [earlier, rerun])).toBe(documentHistoryWhen(earlier.at))
    expect(documentHistorySupersedes(rerun, [rerun])).toBe('#7')
    expect(documentHistorySupersedes(rerun, [review('7', null), rerun])).toBe('#7')
    expect(documentHistorySupersedes(earlier, [earlier, rerun])).toBeNull()
  })
})
