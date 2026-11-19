/**
 * flowgate.v02test.0051 C — what the Q&A API now keeps of the server's record: the automatic responder block, the
 * past-answer search summary (searched / found count / reused answer and its source), the 200 `reused_answer` of an ask
 * and the 409 `existing_answer_requires_reason` refusal. A legacy response (no block) is "no record", never "searched".
 */
import { describe, expect, it, vi } from 'vitest'

vi.mock('@shared/api', () => ({ getRequest: vi.fn(), postRequest: vi.fn() }))

import { qaErrorOf, toQaAskResult, toQaThread } from '@/features/document'

const DOC = 'flowgate.v02test.0051.0001-R'
const thread = (item: Record<string, unknown>) =>
  toQaThread({ ok: true, qa: { doc_id: DOC, status: 'pending', items: [{ id: 1, seq: 1, body: 'q', asker_kind: 'ai', ...item }] } }).items[0]

describe('responder (item.responder)', () => {
  it.each([
    ['dispatched', { state: 'dispatched', run_id: 'air_7', attempts: 1, updated_at: 't' }, { state: 'dispatched', runId: 'air_7', errorCode: null, errorMessage: null, attempts: 1, updatedAt: 't' }],
    ['failed', { state: 'failed', error_code: 'no_independent_responder', error_message: 'none', attempts: 2 }, { state: 'failed', runId: null, errorCode: 'no_independent_responder', errorMessage: 'none', attempts: 2, updatedAt: null }],
    ['user_decision', { state: 'user_decision' }, { state: 'user_decision', runId: null, errorCode: null, errorMessage: null, attempts: 0, updatedAt: null }],
  ])('%s keeps its state, error code and message', (_name, raw, expected) => {
    expect(thread({ responder: raw }).responder).toEqual(expected)
  })

  it.each([[undefined], [null], [{}], [{ state: '' }], ['x']])('%j is no responder', (raw) => {
    expect(thread({ responder: raw }).responder).toBeNull()
  })
})

describe('search summary (item.search_summary)', () => {
  const none = { recorded: false, searched: null, status: null, foundCount: null, reusedAnswer: null, reuseRejectionReason: null }

  it('an absent block, an empty one and the server\'s `no_record` are "no record"', () => {
    expect(thread({}).searchSummary).toEqual(none)
    expect(thread({ search_summary: {} }).searchSummary).toEqual(none)
    expect(thread({ search_summary: { searched: null, status: 'no_record', label: '기록 없음' } }).searchSummary).toEqual(none)
  })

  it('keeps searched, the found count and the reused answer with its source', () => {
    const summary = thread({
      search_summary: {
        precheck_id: 'qpc_1', searched: true, status: 'registered', found_count: 4, returned_count: 4,
        reused_answer: { answer_id: 9, doc_id: 'flowgate.v02test.0036.0007-TR', seq: 1, author_label: 'Claude Opus 5.5' },
        reuse_rejection_reason: '대상이 다름',
      },
    }).searchSummary
    expect(summary).toEqual({
      recorded: true, searched: true, status: 'registered', foundCount: 4,
      reusedAnswer: { answerId: 9, docId: 'flowgate.v02test.0036.0007-TR', seq: 1, answerer: 'Claude Opus 5.5', answerBody: null },
      reuseRejectionReason: '대상이 다름',
    })
  })

  it('a failed search has no count (it is not "0 found")', () => {
    expect(thread({ search_summary: { searched: true, status: 'search_failed', found_count: null } }).searchSummary).toMatchObject({ recorded: true, status: 'search_failed', foundCount: null })
  })

  it('the answerer falls back to the provider snapshot, then the author name', () => {
    const reused = (row: Record<string, unknown>) => thread({ search_summary: { searched: true, status: 'registered', reused_answer: { answer_id: 1, ...row } } }).searchSummary.reusedAnswer
    expect(reused({ author_provider: { ai_provider_name: 'P' } })?.answerer).toBe('P')
    expect(reused({ author_name: 'kim' })?.answerer).toBe('kim')
    expect(reused({})?.answerer).toBeNull()
  })
})

describe('search records of the document (qa.search_prechecks)', () => {
  const read = (precheckList: unknown, items: unknown[] = []) => toQaThread({ ok: true, qa: { doc_id: DOC, status: items.length ? 'pending' : null, items, search_prechecks: precheckList } })

  it('keeps the records that registered no question: the reuse with its source and answer, a failed search', () => {
    const thread = read([
      { precheck_id: 'qpc_a', searched: true, status: 'reused_answer', found_count: 2, returned_count: 2, added_item_ids: [],
        reused_answer: { answer_id: 5, doc_id: 'flowgate.v02test.0036.0007-TR', seq: 2, author_label: 'Codex', answer_body: '기존 답' }, reuse_rejection_reason: null },
      { precheck_id: 'qpc_b', searched: true, status: 'search_failed', found_count: null, returned_count: 0, added_item_ids: [], reused_answer: null },
      { precheck_id: 'qpc_c', searched: true, status: 'registered', found_count: 0, returned_count: 0, added_item_ids: [12] },
    ])
    expect(thread.items).toEqual([])
    expect(thread.searchPrechecks).toEqual([
      { precheckId: 'qpc_a', status: 'reused_answer', searched: true, foundCount: 2, reuseRejectionReason: null, addedItemIds: [],
        reusedAnswer: { answerId: 5, docId: 'flowgate.v02test.0036.0007-TR', seq: 2, answerer: 'Codex', answerBody: '기존 답' } },
      { precheckId: 'qpc_b', status: 'search_failed', searched: true, foundCount: null, reusedAnswer: null, reuseRejectionReason: null, addedItemIds: [] },
      { precheckId: 'qpc_c', status: 'registered', searched: true, foundCount: 0, reusedAnswer: null, reuseRejectionReason: null, addedItemIds: [12] },
    ])
  })

  it.each([[undefined], [null], ['x'], [{}]])('%j (older server) is no record at all', (raw) => {
    expect(read(raw).searchPrechecks).toEqual([])
  })
})

describe('ask: 200 reused_answer and 409 existing_answer_requires_reason', () => {
  it('a 200 reused_answer registered nothing and names the source', () => {
    const result = toQaAskResult({
      ok: true, doc_id: DOC, status: 'reused_answer', added_item_ids: [],
      reused_answer: { answer_id: 5, doc_id: 'flowgate.v02test.0036.0007-TR', seq: 2, author_label: '미확인' },
    })
    expect(result.itemIds).toEqual([])
    expect(result.reusedAnswer).toEqual({ answerId: 5, docId: 'flowgate.v02test.0036.0007-TR', seq: 2, answerer: '미확인', answerBody: null })
  })

  it('a 200 reused_answer keeps the whole search record: id, found count, the answer text and its source', () => {
    const result = toQaAskResult({
      ok: true, doc_id: DOC, status: 'reused_answer', added_item_ids: [], precheck_id: 'qpc_9', searched: true, found_count: 3, returned_count: 3,
      reused_answer: { answer_id: 5, doc_id: 'flowgate.v02test.0036.0007-TR', seq: 2, author_label: 'Claude Opus 5.5', answer_body: '완료 기준은 A입니다.' },
      item_results: [],
    })
    expect(result.precheck).toEqual({
      precheckId: 'qpc_9', status: 'reused_answer', searched: true, foundCount: 3,
      reusedAnswer: { answerId: 5, docId: 'flowgate.v02test.0036.0007-TR', seq: 2, answerer: 'Claude Opus 5.5', answerBody: '완료 기준은 A입니다.' },
      reuseRejectionReason: null, addedItemIds: [],
    })
  })

  it('a registered question carries no reused answer', () => {
    const registered = toQaAskResult({ ok: true, doc_id: DOC, status: 'registered', added_item_ids: [3], reused_answer: null })
    expect(registered.reusedAnswer).toBeNull()
    expect(registered.precheck).toBeNull()
  })

  it('the 409 is its own kind (not a disposed group), whatever the envelope', () => {
    const flat = { isAxiosError: true, response: { status: 409, data: { ok: false, http_status: 409, error_message: 'existing_answer_requires_reason', code: 'existing_answer_requires_reason' } } }
    expect(qaErrorOf(flat)).toEqual({ kind: 'reuse_required', status: 409, code: 'existing_answer_requires_reason' })
    const detail = { isAxiosError: true, response: { status: 409, data: { detail: { code: 'existing_answer_requires_reason' } } } }
    expect(qaErrorOf(detail).kind).toBe('reuse_required')
    // A 409 without a code is still the disposed group.
    expect(qaErrorOf({ isAxiosError: true, response: { status: 409, data: { ok: false, http_status: 409, error_message: 'disposed' } } }).kind).toBe('disposed')
  })
})
