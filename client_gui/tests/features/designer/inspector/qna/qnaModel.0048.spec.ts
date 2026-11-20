/**
 * flowgate.v02.0048 T#7 — the Q&A rules as tables (D#1 §8.5 form, §10.4): which question waits for
 * the user and the tab number (asker_kind × answer_count × answers), the display order, who asked /
 * answered, answering availability with the read-only rows (ownership conflict, revision view),
 * disposed, AI running and an answer in flight, an answer's readiness, and the reason / failure /
 * error keys in the three locales.
 * flowgate.v02.0070 T#5 (B5) — the body context (card line × history × superseded), answering and asking an AI closed
 * by it with the window's one line, the item view's created_at and document id, and the new keys in the three locales.
 */
import { describe, expect, it } from 'vitest'
import qnaMessages from '@/features/designer/inspector/features/qna/messages'
import {
  QNA_OPERATION_CLASS,
  qnaAnswerReady,
  qnaAnswered,
  qnaAvailability,
  qnaBodyContext,
  qnaBodyLine,
  qnaDisabledReason,
  qnaErrorKey,
  qnaFailureKey,
  qnaItemState,
  qnaItemView,
  qnaSide,
  qnaSorted,
  qnaTabCount,
  qnaView,
  qnaWaitingCount,
  qnaWaitingForUser,
  type QnaBodyContext,
  type QnaBodyFacts,
  type QnaFailureKind,
  type QnaInput,
  type QnaItemFacts,
} from '@/features/designer/inspector/features/qna/qnaModel'
import type { InspectorReadOnly } from '@/features/designer/inspector/inspectorTarget'

const ANSWER = { id: 1, body: 'ok', authorKind: 'human', selectedOptionIds: [], provider: null }

function item(fields: Partial<QnaItemFacts> & Pick<QnaItemFacts, 'id'>): QnaItemFacts {
  return { seq: fields.id, title: null, body: `q${fields.id}`, askerKind: 'ai', options: [], answerCount: 0, answers: [], provider: null, ...fields }
}

describe('waiting for the user (flowgate.v02.0048 T#7)', () => {
  it.each([
    // asker_kind, answer_count, answers, answered, waiting, state
    ['ai', 0, 0, false, true, 'waiting'],
    ['ai', 1, 1, true, false, 'answered'],
    ['ai', 1, 0, true, false, 'answered'],
    ['ai', 0, 1, true, false, 'answered'],
    ['ai', 3, 3, true, false, 'answered'],
    ['human', 0, 0, false, false, 'open'],
    ['human', 1, 1, true, false, 'answered'],
    ['', 0, 0, false, false, 'open'],
    ['robot', 0, 0, false, false, 'open'],
  ] as const)('asker %j, answer_count %d, answers %d -> answered %s, waiting %s, %s', (askerKind, answerCount, answers, answered, waiting, state) => {
    const facts = { askerKind, answerCount, answers: Array.from({ length: answers }, () => ANSWER) }
    expect(qnaAnswered(facts)).toBe(answered)
    expect(qnaWaitingForUser(facts)).toBe(waiting)
    expect(qnaItemState(facts)).toBe(state)
  })
})

describe('tab number rule (flowgate.v02.0048 T#7)', () => {
  const waiting = item({ id: 1 })
  const answeredAi = item({ id: 2, answerCount: 1, answers: [ANSWER] })
  const human = item({ id: 3, askerKind: 'human' })
  it.each([
    ['not read yet', null, null, null],
    ['no questions', [], 0, null],
    ['one AI question waiting', [waiting], 1, 1],
    ['AI waiting + answered AI + human unanswered', [waiting, answeredAi, human], 1, 1],
    ['only answered and human questions', [answeredAi, human], 0, null],
    ['three waiting', [waiting, item({ id: 4 }), item({ id: 5 })], 3, 3],
    ['answer_count lags behind answers', [item({ id: 6, answers: [ANSWER] })], 0, null],
  ] as const)('%s -> waiting %s, tab %s', (_label, items, count, tab) => {
    expect(qnaWaitingCount(items as readonly QnaItemFacts[] | null)).toBe(count)
    expect(qnaTabCount(items as readonly QnaItemFacts[] | null)).toBe(tab)
  })
})

describe('display order (flowgate.v02.0048 T#7)', () => {
  it('waiting first, then open, then answered; each by seq then id; the input is not changed', () => {
    const items = [
      item({ id: 1, seq: 1, answerCount: 1, answers: [ANSWER] }),
      item({ id: 2, seq: 2, askerKind: 'human' }),
      item({ id: 3, seq: 5 }),
      item({ id: 4, seq: 3 }),
      item({ id: 6, seq: null }),
      item({ id: 5, seq: 3 }),
    ]
    expect(qnaSorted(items).map((entry) => entry.id)).toEqual([4, 5, 3, 6, 2, 1])
    expect(items.map((entry) => entry.id)).toEqual([1, 2, 3, 4, 6, 5])
  })
})

describe('asker / answerer side (flowgate.v02.0048 T#7)', () => {
  it.each([
    ['human', null, { kind: 'human', key: 'side.human', params: {} }],
    ['human', { providerName: 'ignored' }, { kind: 'human', key: 'side.human', params: {} }],
    ['ai', { providerName: 'Claude Opus 5.5' }, { kind: 'ai', key: 'side.ai_named', params: { name: 'Claude Opus 5.5' } }],
    ['ai', { providerName: null }, { kind: 'ai', key: 'side.ai', params: {} }],
    ['ai', null, { kind: 'ai', key: 'side.ai', params: {} }],
    ['bot', null, { kind: 'unknown', key: 'side.unknown', params: { kind: 'bot' } }],
  ] as const)('%s with %j', (kind, provider, side) => {
    expect(qnaSide(kind, provider)).toEqual(side)
  })

  it('an item view shows the asker, each answer side and the picked option labels', () => {
    const view = qnaItemView(
      item({
        id: 7,
        options: [
          { id: 'o1', label: '코드 품질' },
          { id: 'o2', label: '성능' },
        ],
        provider: { providerName: 'Claude Opus 5.5' },
        answerCount: 2,
        answers: [
          { id: 30, body: '성능', authorKind: 'human', selectedOptionIds: ['o2'], provider: null },
          { id: null, body: '보완', authorKind: 'ai', selectedOptionIds: ['gone'], provider: null },
        ],
      }),
    )
    expect(view.state).toBe('answered')
    expect(view.answerable).toBe(false)
    expect(view.asker.key).toBe('side.ai_named')
    expect(view.answers).toEqual([
      { key: 'a30', body: '성능', side: { kind: 'human', key: 'side.human', params: {} }, picked: ['성능'] },
      { key: 'i1', body: '보완', side: { kind: 'ai', key: 'side.ai', params: {} }, picked: [] },
    ])
    expect(qnaView([item({ id: 9, answerCount: 1 }), item({ id: 8 })]).map((entry) => [entry.id, entry.answerable])).toEqual([
      [8, true],
      [9, false],
    ])
  })
})

describe('answering availability (flowgate.v02.0048 T#7)', () => {
  const base: QnaInput = { readOnly: null, groupDisposed: false, aiRunning: false, busy: false }

  it('reading is a query, answering a change (D#1 §8.3 rule 0)', () => {
    expect(QNA_OPERATION_CLASS).toEqual({ read: 'query', answer: 'change', ask: 'change', ai_request: 'change' })
  })

  it.each([
    // D#1 §8.5 read-only rows. flowgate.v02.0063: a revision view answers as the draft view does; a Flow whose
    // answers stay closed (conflict, disposed) shows no answer, question or AI line.
    ['v01 / draft (none)', { readOnly: null }, true, null, true],
    ['ownership conflict', { readOnly: 'conflict' }, false, 'reason.read_only_conflict', false],
    ['graph revision view', { readOnly: 'revision' }, true, null, true],
    ['conflict + revision-like facts', { readOnly: 'conflict', groupDisposed: true, aiRunning: true, busy: true }, false, 'reason.read_only_conflict', false],
    ['revision + disposed + AI running', { readOnly: 'revision', groupDisposed: true, aiRunning: true }, false, 'reason.disposed', false],
    ['revision + AI running', { readOnly: 'revision', aiRunning: true }, false, 'reason.ai_running', true],
    ['disposed', { groupDisposed: true }, false, 'reason.disposed', false],
    ['disposed + AI running + busy', { groupDisposed: true, aiRunning: true, busy: true }, false, 'reason.disposed', false],
    ['AI running', { aiRunning: true }, false, 'reason.ai_running', true],
    ['AI running + busy', { aiRunning: true, busy: true }, false, 'reason.ai_running', true],
    ['answer in flight', { busy: true }, false, 'reason.busy', true],
  ] as const)('%s -> enabled %s, %s, shown %s', (_label, fields, enabled, reason, shown) => {
    const input = { ...base, ...(fields as Partial<QnaInput>) }
    expect(qnaDisabledReason(input)).toBe(reason)
    expect(qnaAvailability(input)).toEqual({
      read: { visible: true, enabled: true, reason: null },
      answer: { visible: shown, enabled, reason },
      // flowgate.v02.0053 T#6: asking closes on the same reasons (no question is on its way here).
      ask: { visible: shown, enabled, reason: reason === null ? null : reason.replace('reason.', 'ask_reason.') },
      // flowgate.v02.0056 F1: asking an AI closes on the same reasons (no request is on its way here).
      ai_request: { visible: shown, enabled, reason: reason === null ? null : reason.replace('reason.', 'ai_reason.') },
    })
  })

  it.each(['conflict', 'revision', null] as InspectorReadOnly[])('reading stays enabled when read only is %s', (readOnly) => {
    expect(qnaAvailability({ ...base, readOnly, groupDisposed: true, aiRunning: true, busy: true }).read.enabled).toBe(true)
  })
})

describe('answer readiness (flowgate.v02.0048 T#7)', () => {
  const withOptions = { options: [{ id: 'o1', label: 'A' }, { id: 'o2', label: 'B' }] }
  it.each([
    ['prose', withOptions, '답변', [], true],
    ['blanks only', withOptions, '   \n', [], false],
    ['one known option', withOptions, '', ['o1'], true],
    ['option + prose', withOptions, '이유', ['o2'], true],
    ['unknown option', withOptions, '', ['o9'], false],
    ['unknown option with prose', withOptions, '이유', ['o9'], false],
    ['two options', withOptions, '', ['o1', 'o2'], false],
    ['nothing', { options: [] }, '', [], false],
  ] as const)('%s -> %s', (_label, facts, body, picked, ready) => {
    expect(qnaAnswerReady(facts, body, picked)).toBe(ready)
  })
})

describe('keys and wording (flowgate.v02.0048 T#7)', () => {
  const kinds: QnaFailureKind[] = ['running', 'no_provider', 'disposed', 'forbidden', 'not_found', 'invalid', 'network', 'other']
  const lookup = (messages: Record<string, unknown>, key: string) =>
    key.split('.').reduce<unknown>((node, part) => (node && typeof node === 'object' ? (node as Record<string, unknown>)[part] : undefined), messages)

  it('failure and error keys', () => {
    expect(kinds.map(qnaFailureKey)).toEqual(kinds.map((kind) => `failure.${kind}`))
    expect(kinds.map(qnaErrorKey)).toEqual(['error.other', 'error.other', 'error.other', 'error.forbidden', 'error.not_found', 'error.other', 'error.network', 'error.other'])
  })

  it.each(['ko', 'en', 'ja'] as const)('every key the rules produce has %s wording', (locale) => {
    const keys = [
      ...['read_only_conflict', 'disposed', 'ai_running', 'busy'].map((reason) => `reason.${reason}`),
      ...kinds.map(qnaFailureKey),
      ...kinds.map(qnaErrorKey),
      // 0052 T#1: the tab shows two chips for the three model states (waiting / open -> unanswered).
      ...['unanswered', 'answered'].map((state) => `state.${state}`),
      'side.human',
      'side.ai',
      'side.ai_named',
      'side.unknown',
    ]
    for (const key of keys) expect(typeof lookup(qnaMessages[locale] as Record<string, unknown>, key), `${locale} ${key}`).toBe('string')
  })
})

describe('the body of the document before answering (flowgate.v02.0070 T#5)', () => {
  const DOC = 'flowgate.v02.0070.0001-B'
  const line = (fields: Record<string, unknown> = {}) => ({ docId: DOC, docType: 'B', hasBody: true, unreadable: false, ...fields })
  const base: QnaInput = { readOnly: null, groupDisposed: false, aiRunning: false, busy: false }

  it.each([
    ['its card line has a body', { lines: [line()] }, 'readable'],
    ['an M line (always a body) whose has_body was not sent', { lines: [line({ docType: 'M', hasBody: undefined })] }, 'readable'],
    ['the body read failed', { lines: [line({ unreadable: true })] }, 'unreadable'],
    ['the body read failed on a line without a body', { lines: [line({ unreadable: true, hasBody: false })] }, 'unreadable'],
    ['a blank document', { lines: [line({ hasBody: false })] }, 'unconfirmed'],
    ['has_body not sent', { lines: [line({ hasBody: undefined })] }, 'unconfirmed'],
    ['only another document’s line', { lines: [line({ docId: 'flowgate.v02.0070.0002-N' })] }, 'unconfirmed'],
    ['no card line', { lines: [] }, 'unconfirmed'],
    ['no document', { docId: null, lines: [line({ docId: null })] }, 'unconfirmed'],
    ['history (an earlier document opened exactly)', { lines: [line()], history: true }, 'history'],
    ['a superseded document', { lines: [line()], superseded: true }, 'history'],
    ['history wins over an unreadable body', { lines: [line({ unreadable: true })], history: true }, 'history'],
    // The document read: back and not superseded (false), or not known (null: not back, or failed).
    ['a body of a document read as current', { lines: [line()], superseded: false }, 'readable'],
    ['a body of a document not known to be current (its read not back or failed)', { lines: [line()], superseded: null }, 'unconfirmed'],
    ['an M line of a document not known to be current', { lines: [line({ docType: 'M', hasBody: undefined })], superseded: null }, 'unconfirmed'],
    ['an unreadable body while the document read is not back', { lines: [line({ unreadable: true })], superseded: null }, 'unreadable'],
    ['history while the document read is not back', { lines: [line()], history: true, superseded: null }, 'history'],
  ] as const)('%s -> %s', (_label, facts, context) => {
    expect(qnaBodyContext({ docId: DOC, ...(facts as Partial<QnaBodyFacts>) } as QnaBodyFacts)).toBe(context)
  })

  it.each([
    ['readable', null, null],
    ['unreadable', 'reason.body_unreadable', 'reason.body_unreadable'],
    ['unconfirmed', 'reason.body_unconfirmed', 'reason.body_unconfirmed'],
    ['history', 'reason.history', null],
  ] as const)('body %s: answering and asking an AI closed with %s (the window line %s); asking a question as before', (body, reason, windowLine) => {
    const availability = qnaAvailability({ ...base, body: body as QnaBodyContext })
    const closed = reason === null ? { visible: true, enabled: true, reason: null } : { visible: false, enabled: false, reason }
    expect(availability.answer).toEqual(closed)
    expect(availability.ai_request).toEqual(closed)
    expect(availability.ask).toEqual({ visible: true, enabled: true, reason: null })
    expect(availability.read).toEqual({ visible: true, enabled: true, reason: null })
    expect(qnaBodyLine(availability)).toBe(windowLine)
  })

  it('no body given (a rule table) adds no condition; a Flow whose answers stay closed keeps its own reason and no line', () => {
    expect(qnaAvailability(base).answer).toEqual({ visible: true, enabled: true, reason: null })
    for (const fields of [{ readOnly: 'conflict' as const }, { groupDisposed: true }]) {
      const availability = qnaAvailability({ ...base, ...fields, body: 'unreadable' })
      expect(availability.answer.visible).toBe(false)
      expect(availability.answer.reason).toBe(qnaDisabledReason({ ...base, ...fields }))
      expect(qnaBodyLine(availability)).toBeNull()
    }
  })

  it('a readable body keeps the other reasons (an AI run, an answer in flight)', () => {
    expect(qnaAvailability({ ...base, aiRunning: true, body: 'readable' }).answer).toEqual({ visible: true, enabled: false, reason: 'reason.ai_running' })
    expect(qnaAvailability({ ...base, busy: true, body: 'readable' }).ai_request).toEqual({ visible: true, enabled: false, reason: 'ai_reason.busy' })
  })

  it('the item view carries when the question was registered and the document it belongs to', () => {
    const [view] = qnaView([item({ id: 1, createdAt: '2026-10-18T09:00:00+09:00' })], DOC)
    expect([view.createdAt, view.docId]).toEqual(['2026-10-18T09:00:00+09:00', DOC])
    expect(qnaItemView(item({ id: 2 }))).toMatchObject({ createdAt: null, docId: null })
  })

  it.each(['ko', 'en', 'ja'] as const)('the window context and the body reasons have %s wording', (locale) => {
    const lookup = (messages: Record<string, unknown>, key: string) =>
      key.split('.').reduce<unknown>((node, part) => (node && typeof node === 'object' ? (node as Record<string, unknown>)[part] : undefined), messages)
    for (const key of ['reason.body_unreadable', 'reason.body_unconfirmed', 'reason.history', 'detail.doc', 'detail.asked_at', 'options_list']) {
      expect(typeof lookup(qnaMessages[locale] as Record<string, unknown>, key), `${locale} ${key}`).toBe('string')
    }
  })
})
