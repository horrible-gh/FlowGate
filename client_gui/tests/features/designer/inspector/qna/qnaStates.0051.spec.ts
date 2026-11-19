/**
 * flowgate.v02test.0051 C — the Q&A rules of the four unanswered states (대기 중 / AI 답변 중 / 기술 실패 / 사람 답변 필요),
 * their order, chip key and tone, the failure lines, the search lines ("기록 없음" for a legacy question), the ask failure
 * keys of the reuse contract, and ko / en / ja wording for every key.
 */
import { describe, expect, it } from 'vitest'
import qnaMessages from '@/features/designer/inspector/features/qna/messages'
import {
  QNA_ASK_REUSED_KEY,
  qnaAiFailureKey,
  qnaAskFailureKey,
  qnaFailureKey,
  qnaItemState,
  qnaItemView,
  qnaNeedsPerson,
  qnaPrecheckLines,
  qnaPrecheckViews,
  qnaResponderLines,
  qnaResponderView,
  qnaSearchLines,
  qnaSearchView,
  qnaSorted,
  qnaStateKey,
  qnaStateTone,
  qnaTabCount,
  qnaWaitingForUser,
  type QnaItemFacts,
  type QnaPrecheckFacts,
  type QnaSearchFacts,
} from '@/features/designer/inspector/features/qna/qnaModel'

const ANSWER = { id: 1, body: 'ok', authorKind: 'ai', selectedOptionIds: [], provider: null }
const item = (fields: Partial<QnaItemFacts> & Pick<QnaItemFacts, 'id'>): QnaItemFacts => ({
  seq: fields.id, title: null, body: `q${fields.id}`, askerKind: 'ai', options: [], answerCount: 0, answers: [], provider: null, ...fields,
})
const lookup = (messages: Record<string, unknown>, key: string) =>
  key.split('.').reduce<unknown>((node, part) => (node && typeof node === 'object' ? (node as Record<string, unknown>)[part] : undefined), messages)
const ko = (key: string, params: Record<string, string | number> = {}) =>
  String(lookup(qnaMessages.ko as Record<string, unknown>, key)).replace(/\{(\w+)\}/g, (_m, name) => String(params[name]))

describe('the state of an unanswered question follows the responder record, not who asked', () => {
  it.each([
    ['an AI asked, no responder record', 'ai', undefined, 'queued'],
    ['an AI asked, responder null', 'ai', null, 'queued'],
    ['dispatched', 'ai', { state: 'dispatched' }, 'ai_answering'],
    ['failed (technical)', 'ai', { state: 'failed', errorCode: 'no_independent_responder' }, 'failed'],
    ['user_decision (only this is a person\'s turn)', 'ai', { state: 'user_decision' }, 'human_needed'],
    ['a person asked, no record', 'human', null, 'open'],
    ['a person asked and requested an AI: dispatched', 'human', { state: 'dispatched' }, 'ai_answering'],
    ['a state this screen does not know falls back by asker (AI)', 'ai', { state: 'zzz' }, 'queued'],
    ['a state this screen does not know falls back by asker (person)', 'human', { state: 'zzz' }, 'open'],
  ] as const)('%s -> %s', (_label, askerKind, responder, state) => {
    expect(qnaItemState({ askerKind, answerCount: 0, answers: [], responder })).toBe(state)
  })

  it('an answer wins over every responder state', () => {
    for (const state of ['dispatched', 'failed', 'user_decision', 'answered']) {
      expect(qnaItemState({ askerKind: 'ai', answerCount: 1, answers: [], responder: { state } })).toBe('answered')
      expect(qnaItemState({ askerKind: 'ai', answerCount: 0, answers: [ANSWER], responder: { state } })).toBe('answered')
    }
  })

  it('only user_decision needs a person; a technical failure does not become "person needed"', () => {
    const needs = (state: string) => qnaNeedsPerson({ askerKind: 'ai', answerCount: 0, answers: [], responder: { state } })
    expect([needs('user_decision'), needs('failed'), needs('dispatched')]).toEqual([true, false, false])
  })

  it('the tab number still counts the open AI questions whatever their state', () => {
    const items = [item({ id: 1 }), item({ id: 2, responder: { state: 'failed' } }), item({ id: 3, askerKind: 'human' }), item({ id: 4, answerCount: 1, answers: [ANSWER] })]
    expect(items.filter(qnaWaitingForUser)).toHaveLength(2)
    expect(qnaTabCount(items)).toBe(2)
  })

  it('order: person needed, failed, AI answering, queued, open, answered; each by seq then id', () => {
    const items = [
      item({ id: 1, seq: 1, answerCount: 1, answers: [ANSWER] }),
      item({ id: 2, seq: 2, askerKind: 'human' }),
      item({ id: 3, seq: 3 }),
      item({ id: 4, seq: 4, responder: { state: 'dispatched' } }),
      item({ id: 5, seq: 5, responder: { state: 'failed' } }),
      item({ id: 6, seq: 6, responder: { state: 'user_decision' } }),
      item({ id: 7, seq: 0, responder: { state: 'failed' } }),
    ]
    expect(qnaSorted(items).map((entry) => entry.id)).toEqual([6, 7, 5, 4, 3, 2, 1])
  })

  it('chip key and tone: the four states differ from each other and from 미답변', () => {
    const states = ['queued', 'ai_answering', 'failed', 'human_needed', 'open', 'answered'] as const
    expect(states.map(qnaStateKey)).toEqual(['state.queued', 'state.ai_answering', 'state.failed', 'state.human_needed', 'state.unanswered', 'state.answered'])
    expect(states.map(qnaStateTone)).toEqual(['info', 'running', 'danger', 'warning', 'neutral', 'success'])
  })
})

describe('a failed question shows the server\'s code and message', () => {
  it('only a failed responder has a failure view; nothing is guessed', () => {
    expect(qnaResponderView({ responder: { state: 'dispatched' } })).toBeNull()
    expect(qnaResponderView({ responder: null })).toBeNull()
    expect(qnaResponderView({ responder: { state: 'failed', errorCode: 'c', errorMessage: 'm', attempts: 2 } })).toEqual({ errorCode: 'c', errorMessage: 'm', attempts: 2 })
    expect(qnaResponderView({ responder: { state: 'failed' } })).toEqual({ errorCode: null, errorMessage: null, attempts: 0 })
  })

  it('lines: code, message, attempts; "오류 코드 없음" when the server sent none', () => {
    expect(qnaResponderLines({ errorCode: 'no_independent_responder', errorMessage: '답변 AI 없음', attempts: 2 }, ko)).toEqual([
      '오류 코드: no_independent_responder', '오류 내용: 답변 AI 없음', '시도 2회',
    ])
    expect(qnaResponderLines({ errorCode: null, errorMessage: null, attempts: 0 }, ko)).toEqual(['오류 코드 없음'])
  })

  it('the item view carries the failure only while the question is failed', () => {
    const failed = qnaItemView(item({ id: 1, responder: { state: 'failed', errorCode: 'c' } }))
    expect([failed.state, failed.responder?.errorCode]).toEqual(['failed', 'c'])
    expect(qnaItemView(item({ id: 2, responder: { state: 'user_decision' } })).responder).toBeNull()
    expect(qnaItemView(item({ id: 3, responder: { state: 'failed' }, answerCount: 1, answers: [ANSWER] })).responder).toBeNull()
  })
})

describe('the search record: searched / found count / reused answer, "기록 없음" for a legacy question', () => {
  const fact = (fields: Partial<QnaSearchFacts>): QnaSearchFacts => ({ recorded: true, searched: true, status: 'registered', foundCount: 0, reusedAnswer: null, reuseRejectionReason: null, ...fields })
  const lines = (summary: QnaSearchFacts | null | undefined) => qnaSearchLines(qnaSearchView({ searchSummary: summary }), ko)

  it.each([
    ['absent', undefined],
    ['null', null],
    ['recorded false', fact({ recorded: false, searched: null })],
  ])('%s -> 검색 기록 없음 (never "검색함")', (_label, summary) => {
    expect(lines(summary)).toEqual(['검색 기록 없음'])
    expect(qnaSearchView({ searchSummary: summary }).kind).toBe('no_record')
  })

  it('searched with zero, some and unknown counts', () => {
    expect(lines(fact({ foundCount: 0 }))).toEqual(['검색함 · 찾은 0건', '재사용한 답 없음'])
    expect(lines(fact({ foundCount: 7 }))).toEqual(['검색함 · 찾은 7건', '재사용한 답 없음'])
    expect(lines(fact({ foundCount: null }))).toEqual(['검색함 · 찾은 건수 미확인', '재사용한 답 없음'])
  })

  it('a failed search and a search that did not run are said so (no count)', () => {
    expect(lines(fact({ status: 'search_failed', foundCount: null }))).toEqual(['검색 실패', '재사용한 답 없음'])
    expect(lines(fact({ searched: false, foundCount: 3 }))).toEqual(['검색 안 함', '재사용한 답 없음'])
    expect(qnaSearchView({ searchSummary: fact({ searched: false, foundCount: 3 }) }).foundCount).toBeNull()
  })

  it('the reused answer shows its document, Q number and answerer; a missing answerer is 미확인', () => {
    const reused = { answerId: 9, docId: 'flowgate.v02test.0036.0007-TR', seq: 1, answerer: 'Claude Opus 5.5' }
    expect(lines(fact({ foundCount: 2, reusedAnswer: reused, reuseRejectionReason: '대상이 다름' }))).toEqual([
      '검색함 · 찾은 2건',
      '재사용한 답: flowgate.v02test.0036.0007-TR Q1 · 답변자 Claude Opus 5.5',
      '기존 답을 쓰지 않은 사유: 대상이 다름',
    ])
    expect(lines(fact({ reusedAnswer: { ...reused, answerer: null } }))[1]).toContain('답변자 미확인')
    expect(lines(fact({ reusedAnswer: { answerId: 1, docId: null, seq: null, answerer: null } }))[1]).toBe('재사용한 답: 출처 미확인')
  })
})

describe('reuse contract keys and wording', () => {
  it('a 409 existing_answer_requires_reason is not a refusal of an answer or an AI request', () => {
    expect(qnaAskFailureKey('reuse_required')).toBe('ask_failure.reuse_required')
    expect(qnaFailureKey('reuse_required')).toBe('failure.other')
    expect(qnaAiFailureKey('reuse_required')).toBe('ai_failure.other')
    expect(QNA_ASK_REUSED_KEY).toBe('ask_failure.reused')
  })

  it.each(['ko', 'en', 'ja'] as const)('%s has wording for every new key', (locale) => {
    const keys = [
      ...['queued', 'ai_answering', 'failed', 'human_needed'].map((state) => `state.${state}`),
      'ask_failure.reuse_required', 'ask_failure.reused', 'wait_loop', 'wait_user_decision',
      ...['responder_code', 'responder_message', 'responder_attempts', 'responder_unknown'].map((key) => `detail.${key}`),
      ...['no_record', 'not_searched', 'failed', 'searched', 'searched_unknown', 'reused', 'reused_none', 'source', 'source_unknown', 'who_unknown', 'rejection'].map((key) => `search.${key}`),
      ...['reused_answer', 'needs_reason', 'search_failed', 'not_registered'].map((key) => `precheck.outcome.${key}`),
      'precheck.answer',
    ]
    for (const key of keys) expect(typeof lookup(qnaMessages[locale] as Record<string, unknown>, key), `${locale} ${key}`).toBe('string')
  })

  it('the four states read differently in every language', () => {
    for (const locale of ['ko', 'en', 'ja'] as const) {
      const words = ['queued', 'ai_answering', 'failed', 'human_needed', 'unanswered'].map((state) => lookup(qnaMessages[locale] as Record<string, unknown>, `state.${state}`))
      expect(new Set(words).size, locale).toBe(5)
    }
  })
})

describe('searches that registered no question (search_prechecks)', () => {
  const record = (fields: Partial<QnaPrecheckFacts>): QnaPrecheckFacts => ({
    precheckId: 'qpc_1', status: 'reused_answer', searched: true, foundCount: 3, reusedAnswer: null, reuseRejectionReason: null, addedItemIds: [], ...fields,
  })
  const reused = { answerId: 5, docId: 'flowgate.v02test.0036.0007-TR', seq: 2, answerer: 'Codex', answerBody: '기존 답입니다.' }

  it('lists only the searches that registered nothing, newest first; one that registered a question is that question\'s own line', () => {
    const views = qnaPrecheckViews([
      record({ precheckId: 'a' }),
      record({ precheckId: 'b', status: 'registered', addedItemIds: [7] }),
      record({ precheckId: 'c', status: 'search_failed', foundCount: null }),
    ])
    expect(views.map((view) => [view.precheckId, view.outcome])).toEqual([['c', 'search_failed'], ['a', 'reused_answer']])
    expect(qnaPrecheckViews(null)).toEqual([])
    expect(qnaPrecheckViews(undefined)).toEqual([])
  })

  it.each([
    ['reused_answer', 'reused_answer'],
    ['existing_answer_requires_reason', 'needs_reason'],
    ['search_failed', 'search_failed'],
    ['registration_rolled_back', 'not_registered'],
    ['batch_blocked', 'not_registered'],
    [null, 'not_registered'],
  ])('status %s reads as %s', (status, outcome) => {
    expect(qnaPrecheckViews([record({ status })])[0]!.outcome).toBe(outcome)
  })

  it('a reuse shows what came of it, the found count, the source (document · Q · answerer) and the answer itself', () => {
    const [view] = qnaPrecheckViews([record({ reusedAnswer: reused })])
    expect(qnaPrecheckLines(view!, ko)).toEqual([
      '기존 답을 재사용함 · 질문 등록 안 됨',
      '검색함 · 찾은 3건',
      '재사용한 답: flowgate.v02test.0036.0007-TR Q2 · 답변자 Codex',
      '재사용한 답 내용: 기존 답입니다.',
    ])
  })

  it('a long reused answer is cut; a reuse without a source says 출처 미확인 and never invents one', () => {
    const long = qnaPrecheckViews([record({ reusedAnswer: { ...reused, answerBody: 'x'.repeat(300) } })])[0]!
    expect(qnaPrecheckLines(long, ko).slice(-1)[0]).toBe(`재사용한 답 내용: ${'x'.repeat(200)}…`)
    const anonymous = qnaPrecheckViews([record({ reusedAnswer: { answerId: 1, docId: null, seq: null, answerer: null } })])[0]!
    expect(qnaPrecheckLines(anonymous, ko)).toContain('재사용한 답: 출처 미확인')
  })

  it('a refused resend and a failed search keep their own count (a failed one has none)', () => {
    const needs = qnaPrecheckViews([record({ status: 'existing_answer_requires_reason', foundCount: 2 })])[0]!
    expect(qnaPrecheckLines(needs, ko)).toEqual(['기존 답을 쓰지 않는 사유가 필요함 · 질문 등록 안 됨', '검색함 · 찾은 2건', '재사용한 답 없음'])
    const failed = qnaPrecheckViews([record({ status: 'search_failed', foundCount: null })])[0]!
    expect(qnaPrecheckLines(failed, ko)).toEqual(['검색 실패 · 질문 등록 안 됨', '검색 실패', '재사용한 답 없음'])
  })
})
