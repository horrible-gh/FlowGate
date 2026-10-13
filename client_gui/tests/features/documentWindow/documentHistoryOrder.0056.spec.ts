/**
 * flowgate.v02.0056 F2-2 — the review history order, fixed whatever order the read sends (O3 0009-TR §4-4 / §6-2):
 * GET /documents/{id} sends `ai_review_history` newest first and `rejection_history` oldest first (F1 took both as
 * "oldest first"). The mapper keeps the server's order (and says so); the window's one timeline is newest first by
 * the instant of each entry — a `Z` time and a `+09:00` time of the same moment are the same time — with entries of
 * one moment and entries without a time newest first within their kind, the reviews before the rejections.
 */
import { describe, expect, it } from 'vitest'
import { toDocumentHistory } from '@/features/document'
import { documentHistoryTimeline } from '@/features/documentWindow/documentHistoryModel'

const review = (id: number, at: string | null) => ({ id, verdict: 'pass', finding_count: 0, findings: [], reviewed_at: at })
const rejection = (id: string, at: string | null) => ({ rejection_id: id, reason: `사유 ${id}`, rejected_at: at })

/** A document read as the server answers it: reviews newest first, rejections oldest first. */
function read(reviews: unknown[], rejections: unknown[]) {
  return toDocumentHistory({ doc_id: 'flowgate.v02.0056.0007-TR', ai_review_history: reviews, rejection_history: rejections })
}

const keys = (history: ReturnType<typeof read>) => documentHistoryTimeline(history).map((entry) => entry.key)

describe('the review history order (flowgate.v02.0056 F2-2)', () => {
  it('the mapper keeps the order the server sends: reviews newest first, rejections oldest first', () => {
    const history = read(
      [review(3, '2026-10-09T18:00:00+09:00'), review(2, '2026-10-09T16:00:00+09:00'), review(1, '2026-10-09T14:00:00+09:00')],
      [rejection('r1', '2026-10-09T15:00:00+09:00'), rejection('r2', '2026-10-09T17:00:00+09:00')],
    )
    expect(history.reviews.map((item) => item.id)).toEqual(['3', '2', '1'])
    expect(history.rejections.map((item) => item.id)).toEqual(['r1', 'r2'])
  })

  it('the timeline is newest first across both kinds, from the server order', () => {
    const history = read(
      [review(3, '2026-10-09T18:00:00+09:00'), review(2, '2026-10-09T16:00:00+09:00'), review(1, '2026-10-09T14:00:00+09:00')],
      [rejection('r1', '2026-10-09T15:00:00+09:00'), rejection('r2', '2026-10-09T17:00:00+09:00')],
    )
    expect(keys(history)).toEqual(['ai_review:3', 'reject:r2', 'ai_review:2', 'reject:r1', 'ai_review:1'])
  })

  it('times are compared as instants: a UTC review after a +09:00 rejection of an earlier moment comes first', () => {
    // 09:30Z = 18:30+09:00, later than the 18:00+09:00 rejection, though "2026-10-09T09…" sorts lower as text.
    const history = read([review(2, '2026-10-09T09:30:00Z')], [rejection('r1', '2026-10-09T18:00:00+09:00')])
    expect(keys(history)).toEqual(['ai_review:2', 'reject:r1'])
  })

  it('entries of one moment: newest first within their kind (rejections reversed from the stored order), reviews first', () => {
    const at = '2026-10-09T15:00:00+09:00'
    const history = read([review(5, at), review(4, at)], [rejection('r1', at), rejection('r2', '2026-10-09T06:00:00Z')])
    // r2 is the same instant written in UTC; it was stored after r1, so it is the newer one.
    expect(keys(history)).toEqual(['ai_review:5', 'ai_review:4', 'reject:r2', 'reject:r1'])
  })

  it('entries without a time go last: reviews as sent, then rejections newest first', () => {
    const history = read([review(2, null), review(1, null)], [rejection('r1', null), rejection('r2', null), rejection('r3', '2026-10-09T10:00:00+09:00')])
    expect(keys(history)).toEqual(['reject:r3', 'ai_review:2', 'ai_review:1', 'reject:r2', 'reject:r1'])
  })

  it('the same history in any order gives the same timeline when every entry has its own time', () => {
    const reviews = [review(3, '2026-10-09T18:00:00+09:00'), review(2, '2026-10-09T16:00:00+09:00'), review(1, '2026-10-09T14:00:00+09:00')]
    const rejections = [rejection('r1', '2026-10-09T15:00:00+09:00'), rejection('r2', '2026-10-09T17:00:00+09:00')]
    expect(keys(read([...reviews].reverse(), [...rejections].reverse()))).toEqual(keys(read(reviews, rejections)))
  })
})
