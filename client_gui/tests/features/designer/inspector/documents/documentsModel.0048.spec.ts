/**
 * flowgate.v02.0048 T#5 — documentsModel as tables (D#1 §8.5, §10.2): what the documents tab shows
 * for the v01 / graph / unassigned / conflict fixtures before and after the read, slot / run rows,
 * rejections and superseded documents newest first, review tones, error keys, and the availability
 * of ↗ 열기 — a query, enabled on every card kind × ownership, read only included.
 */
import { describe, expect, it } from 'vitest'
import {
  DOCUMENTS_OPERATION_KINDS,
  documentsAvailability,
  documentsErrorKey,
  documentsPrevious,
  documentsRejections,
  documentsReviewTone,
  documentsRun,
  documentsSlot,
  documentsTime,
  documentsView,
  type DocumentsDetailInput,
} from '@/features/designer/inspector/features/documents/documentsModel'
import { INSPECTOR_CARD_KINDS, INSPECTOR_OWNERSHIPS, type InspectorTarget } from '@/features/designer/inspector/inspectorTarget'
import { SELECTIONS, targetOf } from '../support/inspectorSupport'

/** The v01 fixture's document as read (the server answer of documentDetailRead). */
const V01_DETAIL: DocumentsDetailInput = {
  docId: 'flowgate.v02.0048.0011-TR',
  typeCode: 'TR',
  seq: 11,
  title: '작업레포트 — T#1',
  status: 'open',
  docReviewStatus: 'rejected',
  revisionNo: 2,
  rejectionReason: '구조 검사 누락',
  rejectionHistory: [
    { rejectionId: 'rj_01', reason: '테스트 결과 누락', rejectedAt: '2026-10-09T14:10:00+09:00', aiResponse: '추가했습니다.' },
    { rejectionId: 'rj_02', reason: '구조 검사 누락', rejectedAt: '2026-10-09T15:00:00+09:00', aiResponse: null },
  ],
  hasAiReview: true,
}

const record = (docId: string, fields: Partial<DocumentsDetailInput> = {}) => ({
  docId,
  typeCode: 'TR',
  seq: 9,
  title: '이전 레포트',
  status: 'closed',
  docReviewStatus: 'rejected',
  revisionNo: 1,
  rejectionReason: null,
  ...fields,
})

describe('documentsView — the fixtures as the tab shows them', () => {
  it('v01, before the read: the Canvas values, the slot rows, the superseded ids pending', () => {
    expect(documentsView(targetOf(SELECTIONS.v01), null, null)).toEqual({
      current: {
        docId: 'flowgate.v02.0048.0011-TR',
        typeCode: 'TR',
        seq: null,
        title: 'T#1 결과 레포트',
        reviewStatus: 'pending_review',
        reviewTone: 'warning',
        status: 'open',
        revisionNo: null,
        hasAiReview: null,
        rejectionReason: null,
      },
      slot: {
        rows: [
          { key: 'order', value: '4' },
          { key: 'type', value: 'TR' },
          { key: 'label', value: 'T#1 결과' },
          { key: 'derived_status', value: 'in_progress' },
          { key: 'wp_card', value: 'c_t1' },
        ],
        protected: false,
      },
      run: null,
      rejections: [],
      previous: [
        { docId: 'flowgate.v02.0048.0009-TR', state: 'pending', typeCode: null, seq: null, title: null, reviewStatus: null, reviewTone: 'neutral' },
      ],
    })
  })

  it('v01, after the read: the read values win, the rejection reason and history (newest first), the superseded document read', () => {
    const view = documentsView(targetOf(SELECTIONS.v01), V01_DETAIL, [{ docId: 'flowgate.v02.0048.0009-TR', record: record('flowgate.v02.0048.0009-TR') }])
    expect(view?.current).toEqual({
      docId: 'flowgate.v02.0048.0011-TR',
      typeCode: 'TR',
      seq: 11,
      title: '작업레포트 — T#1',
      reviewStatus: 'rejected',
      reviewTone: 'danger',
      status: 'open',
      revisionNo: 2,
      hasAiReview: true,
      rejectionReason: '구조 검사 누락',
    })
    expect(view?.rejections).toEqual([
      { key: 'rj_02', reason: '구조 검사 누락', rejectedAt: '2026-10-09 15:00', aiResponse: null },
      { key: 'rj_01', reason: '테스트 결과 누락', rejectedAt: '2026-10-09 14:10', aiResponse: '추가했습니다.' },
    ])
    expect(view?.previous).toEqual([
      { docId: 'flowgate.v02.0048.0009-TR', state: 'ready', typeCode: 'TR', seq: 9, title: '이전 레포트', reviewStatus: 'rejected', reviewTone: 'danger' },
    ])
  })

  it('graph: no slot; the node run rows, its result document being the bound one (nothing extra to open)', () => {
    const view = documentsView(targetOf(SELECTIONS.graph), null, null)
    expect(view?.current).toMatchObject({ docId: 'flowgate.v02.0049.0007-TS', typeCode: 'TS', reviewStatus: 'approved', reviewTone: 'success', status: 'closed' })
    expect(view?.slot).toBeNull()
    expect(view?.run).toEqual({
      rows: [
        { key: 'status', value: 'completed' },
        { key: 'opened_at', value: '2026-10-09 01:00' },
        { key: 'finished_at', value: '2026-10-09 01:20' },
        { key: 'result_doc', value: 'flowgate.v02.0049.0007-TS' },
      ],
      resultDocId: null,
    })
    expect(view?.previous).toEqual([])
  })

  it('unassigned / conflict: the document as the Canvas gives it; a rejected one shows the last reason read', () => {
    expect(documentsView(targetOf(SELECTIONS.unassigned), null, null)?.current).toMatchObject({ typeCode: 'R', reviewStatus: null, reviewTone: 'neutral', status: 'draft' })
    const conflict = targetOf(SELECTIONS.conflict)
    expect(documentsView(conflict, null, null)?.current).toMatchObject({ reviewStatus: 'rejected', rejectionReason: null })
    const read = { ...V01_DETAIL, docId: conflict.docId!, typeCode: 'D', rejectionReason: null }
    expect(documentsView(conflict, read, [])?.current.rejectionReason).toBe('구조 검사 누락')
  })

  it('a Node without a document has no view (the tab is not registered for it)', () => {
    expect(documentsView(targetOf(SELECTIONS.v01NoDocument), null, null)).toBeNull()
    expect(documentsView(targetOf(SELECTIONS.graphGate), null, null)).toBeNull()
  })

  it("a read of another document (the target changed meanwhile) is not shown as the target's", () => {
    const view = documentsView(targetOf(SELECTIONS.graph), V01_DETAIL, [])
    expect(view?.current).toMatchObject({ docId: 'flowgate.v02.0049.0007-TS', title: '테스트 지시', seq: null, hasAiReview: null })
    expect(view?.rejections).toEqual([])
    expect(view?.previous).toEqual([])
  })

  it.each([
    ['approved with an old reason', 'approved', '옛 사유', null],
    ['pending with an old reason', 'pending_review', '옛 사유', null],
    ['rejected with a reason', 'rejected', '사유', '사유'],
    ['rejected, blank reason, no history', 'rejected', '  ', null],
  ])('rejection reason: %s', (_label, status, reason, expected) => {
    const read = { ...V01_DETAIL, docReviewStatus: status, rejectionReason: reason, rejectionHistory: [] }
    expect(documentsView(targetOf(SELECTIONS.v01), read, [])?.current.rejectionReason).toBe(expected)
  })

  it('an empty read title falls back to the Node title', () => {
    expect(documentsView(targetOf(SELECTIONS.v01), { ...V01_DETAIL, title: ' ' }, [])?.current.title).toBe('T#1 결과 레포트')
  })
})

describe('slot / run / rejections / previous rows', () => {
  it.each([
    ['all fields', { itemId: 1, itemSeq: 2, order: 0, type: 'D', label: '설계', derivedStatus: 'done', protected: true, sourceWpCardId: 'c1' }, ['order:0', 'type:D', 'label:설계', 'derived_status:done', 'wp_card:c1'], true],
    ['empty fields dropped', { itemId: 1, itemSeq: null, order: 3, type: ' ', label: null, derivedStatus: '', protected: false, sourceWpCardId: null }, ['order:3'], false],
  ])('slot: %s', (_label, slot, rows, isProtected) => {
    const view = documentsSlot(slot)
    expect(view?.rows.map((item) => `${item.key}:${item.value}`)).toEqual(rows)
    expect(view?.protected).toBe(isProtected)
  })

  it('slot: none on a Graph Node', () => {
    expect(documentsSlot(null)).toBeNull()
  })

  it.each([
    ['no run', null, 'd', null],
    ['an empty run', { status: null, openedAt: null, finishedAt: null, resultDocId: null }, 'd', null],
    ['running, no result yet', { status: 'running', openedAt: '2026-10-09T01:00:00Z', finishedAt: null, resultDocId: null }, 'd', { rows: ['status:running', 'opened_at:2026-10-09 01:00'], resultDocId: null }],
    ['a result other than the bound document', { status: 'completed', openedAt: null, finishedAt: null, resultDocId: 'r2' }, 'd', { rows: ['status:completed', 'result_doc:r2'], resultDocId: 'r2' }],
  ])('run: %s', (_label, run, docId, expected) => {
    const view = documentsRun(run, docId)
    expect(view && { rows: view.rows.map((item) => `${item.key}:${item.value}`), resultDocId: view.resultDocId }).toEqual(expected)
  })

  it('rejections: newest first, blank reasons dropped, keys stable without a rejection id', () => {
    expect(
      documentsRejections([
        { rejectionId: null, reason: '첫 사유', rejectedAt: 'yesterday', aiResponse: ' ' },
        { rejectionId: null, reason: ' ', rejectedAt: null, aiResponse: null },
        { rejectionId: 'r3', reason: ' 셋째 ', rejectedAt: null, aiResponse: '응답' },
      ]),
    ).toEqual([
      { key: 'r3', reason: '셋째', rejectedAt: null, aiResponse: '응답' },
      { key: '#0', reason: '첫 사유', rejectedAt: 'yesterday', aiResponse: null },
    ])
    expect(documentsRejections(null)).toEqual([])
  })

  it('previous: newest first; pending before the read, ready / missing after it', () => {
    const ids = ['p1', 'p2', 'p1']
    expect(documentsPrevious(ids, null).map((item) => `${item.docId}:${item.state}`)).toEqual(['p2:pending', 'p1:pending'])
    expect(
      documentsPrevious(ids, [
        { docId: 'p1', record: record('p1', { title: '첫 판', docReviewStatus: 'revised' }) },
        { docId: 'p2', record: null },
      ]).map((item) => [item.docId, item.state, item.title, item.reviewTone]),
    ).toEqual([
      ['p2', 'missing', null, 'neutral'],
      ['p1', 'ready', '첫 판', 'info'],
    ])
  })
})

describe('review tone / time / error key', () => {
  it.each([
    ['pending_review', 'warning'],
    ['approved', 'success'],
    ['rejected', 'danger'],
    ['revised', 'info'],
    ['wf_done', 'neutral'],
    [null, 'neutral'],
  ])('review status %s -> %s', (status, tone) => {
    expect(documentsReviewTone(status)).toBe(tone)
  })

  it.each([
    ['2026-10-09T15:00:00+09:00', '2026-10-09 15:00'],
    ['2026-10-09T01:20:59Z', '2026-10-09 01:20'],
    ['어제', '어제'],
    ['', null],
    [null, null],
  ])('time %s -> %s', (value, shown) => {
    expect(documentsTime(value)).toBe(shown)
  })

  it.each([
    ['not_found', 'error.not_found'],
    ['forbidden', 'error.forbidden'],
    ['network', 'error.network'],
    ['conflict', 'error.other'],
    ['other', 'error.other'],
    [null, 'error.other'],
  ])('error kind %s -> %s', (kind, key) => {
    expect(documentsErrorKey(kind)).toBe(key)
  })
})

// --- Availability (D#1 §8.5): the tab only views, so every row is enabled -------------------------

function targetWith(fields: Partial<InspectorTarget>): InspectorTarget {
  return { ...targetOf(SELECTIONS.v01), ...fields }
}

const KIND_TYPE: Record<string, string> = {
  requirement: 'R',
  research: 'NR',
  task: 'TR',
  design: 'D',
  test: 'TSR',
  check: 'AC',
  plan: 'WP',
  other: 'M',
}

const OPEN_ENABLED = { open: { visible: true, enabled: true, reason: null } }

describe('documentsAvailability', () => {
  it('↗ 열기 is a query operation (D#1 §8.3 rule 0)', () => {
    expect(DOCUMENTS_OPERATION_KINDS).toEqual({ open: 'query' })
  })

  it.each(INSPECTOR_CARD_KINDS.flatMap((cardKind) => INSPECTOR_OWNERSHIPS.map((ownership) => [cardKind, ownership] as const)))(
    'card %s × ownership %s: open enabled',
    (cardKind, ownership) => {
      expect(documentsAvailability(targetWith({ cardKind, docType: KIND_TYPE[cardKind], ownership, readOnly: ownership === 'conflict' ? 'conflict' : null }))).toEqual(OPEN_ENABLED)
    },
  )

  it.each([
    ['graph × revision (readOnly revision)', { ownership: 'graph', viewMode: 'revision', readOnly: 'revision' as const }],
    ['graph × draft', { ownership: 'graph', viewMode: 'draft', readOnly: null }],
    ['unassigned × revision', { ownership: 'unassigned', viewMode: 'revision', readOnly: 'revision' as const }],
    ['unassigned × draft', { ownership: 'unassigned', viewMode: 'draft', readOnly: null }],
    ['conflict × revision', { ownership: 'conflict', viewMode: 'revision', readOnly: 'conflict' as const }],
  ])('read only rows — %s: a query stays enabled with no reason', (_label, fields) => {
    expect(documentsAvailability(targetWith(fields))).toEqual(OPEN_ENABLED)
  })

  it('no document (or no target): open is not shown', () => {
    const hidden = { open: { visible: false, enabled: false, reason: null } }
    expect(documentsAvailability(targetOf(SELECTIONS.v01NoDocument))).toEqual(hidden)
    expect(documentsAvailability(null)).toEqual(hidden)
  })
})
