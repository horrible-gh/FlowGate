/**
 * flowgate.v02.0053 T#6 — the Q&A rules for a person's question (0053 CH §2): asking is a change closed
 * on the answering reasons (worded for a question), then on a question or an answer on its way; a
 * question is ready when it is not blank; the refusal keys of a question and their wording in the three
 * locales; no empty-list sentence is left.
 * flowgate.v02.0056 F1 (0056 CH §3) — asking an AI to answer is its own change (`ai_request`): closed on the
 * same reasons (worded for the request), then on a request, a question or an answer on its way; its refusal
 * keys (every kind the q routes and the ai-request admission answer) and their wording.
 */
import { describe, expect, it } from 'vitest'
import qnaMessages from '@/features/designer/inspector/features/qna/messages'
import {
  qnaAiDisabledReason,
  qnaAiFailureKey,
  qnaAskDisabledReason,
  qnaAskFailureKey,
  qnaAskReady,
  qnaAvailability,
  type QnaFailureKind,
  type QnaInput,
} from '@/features/designer/inspector/features/qna/qnaModel'

const base: QnaInput = { readOnly: null, groupDisposed: false, aiRunning: false, busy: false, asking: false, requesting: false }

const lookup = (messages: Record<string, unknown>, key: string) =>
  key.split('.').reduce<unknown>((node, part) => (node && typeof node === 'object' ? (node as Record<string, unknown>)[part] : undefined), messages)

describe('asking availability (flowgate.v02.0053 T#6)', () => {
  it.each([
    ['open', {}, null],
    ['ownership conflict', { readOnly: 'conflict' }, 'ask_reason.read_only_conflict'],
    ['revision view', { readOnly: 'revision' }, 'ask_reason.read_only_revision'],
    ['disposed', { groupDisposed: true, aiRunning: true, asking: true }, 'ask_reason.disposed'],
    ['AI running', { aiRunning: true, asking: true, busy: true }, 'ask_reason.ai_running'],
    ['a question on its way', { asking: true, busy: true }, 'ask_reason.asking'],
    ['an answer on its way', { busy: true }, 'ask_reason.busy'],
  ] as const)('%s -> %s', (_label, fields, reason) => {
    const input = { ...base, ...(fields as Partial<QnaInput>) }
    expect(qnaAskDisabledReason(input)).toBe(reason)
    expect(qnaAvailability(input).ask).toEqual({ visible: true, enabled: reason === null, reason })
  })

  it('a question on its way does not close answering', () => {
    expect(qnaAvailability({ ...base, asking: true }).answer).toEqual({ visible: true, enabled: true, reason: null })
  })

  it('without `asking` (callers before 0053) asking follows answering', () => {
    const { asking: _asking, ...old } = base
    expect(qnaAvailability(old).ask).toEqual({ visible: true, enabled: true, reason: null })
  })

  it.each([
    ['text', '질문', true],
    ['text around blanks', '  질문 \n', true],
    ['blanks only', ' \n\t', false],
    ['nothing', '', false],
  ])('ready: %s -> %s', (_label, body, ready) => {
    expect(qnaAskReady(body)).toBe(ready)
  })
})

describe('AI answer request availability (flowgate.v02.0056 F1)', () => {
  it.each([
    ['open', {}, null],
    ['ownership conflict', { readOnly: 'conflict' }, 'ai_reason.read_only_conflict'],
    ['revision view', { readOnly: 'revision' }, 'ai_reason.read_only_revision'],
    ['disposed', { groupDisposed: true, aiRunning: true, requesting: true }, 'ai_reason.disposed'],
    ['AI running', { aiRunning: true, requesting: true, asking: true }, 'ai_reason.ai_running'],
    ['a request on its way', { requesting: true, asking: true, busy: true }, 'ai_reason.requesting'],
    ['a question on its way', { asking: true, busy: true }, 'ai_reason.asking'],
    ['an answer on its way', { busy: true }, 'ai_reason.busy'],
  ] as const)('%s -> %s', (_label, fields, reason) => {
    const input = { ...base, ...(fields as Partial<QnaInput>) }
    expect(qnaAiDisabledReason(input)).toBe(reason)
    expect(qnaAvailability(input).ai_request).toEqual({ visible: true, enabled: reason === null, reason })
  })

  it('a request on its way closes neither answering nor asking', () => {
    const availability = qnaAvailability({ ...base, requesting: true })
    expect(availability.answer.enabled).toBe(true)
    expect(availability.ask.enabled).toBe(true)
  })
})

describe('refusal keys of a question and of an AI answer request', () => {
  const kinds: QnaFailureKind[] = ['running', 'no_provider', 'disposed', 'forbidden', 'not_found', 'invalid', 'network', 'other']

  it('the question refused: one key per kind (no provider is not a question refusal)', () => {
    expect(kinds.map((kind) => qnaAskFailureKey(kind))).toEqual([
      'ask_failure.running',
      'ask_failure.other',
      'ask_failure.disposed',
      'ask_failure.forbidden',
      'ask_failure.not_found',
      'ask_failure.invalid',
      'ask_failure.network',
      'ask_failure.other',
    ])
  })

  it('the AI request refused: one key per kind the admission or the q routes answer, else other', () => {
    expect(kinds.map((kind) => qnaAiFailureKey(kind))).toEqual([
      'ai_failure.running',
      'ai_failure.no_provider',
      'ai_failure.disposed',
      'ai_failure.forbidden',
      'ai_failure.not_found',
      'ai_failure.other',
      'ai_failure.network',
      'ai_failure.other',
    ])
  })

  it.each(['ko', 'en', 'ja'] as const)('every key of the question line and the AI line has %s wording; no empty-list sentence', (locale) => {
    const messages = qnaMessages[locale] as Record<string, unknown>
    const keys = [
      'ask',
      'ask_placeholder',
      'ask_failed',
      'ai_request',
      'ai_provider',
      'ai_provider_loading',
      'ai_provider_none',
      'ai_waiting',
      'ai_failed',
      'failure.no_provider',
      ...['read_only_conflict', 'read_only_revision', 'disposed', 'ai_running', 'asking', 'busy'].map((reason) => `ask_reason.${reason}`),
      ...['read_only_conflict', 'read_only_revision', 'disposed', 'ai_running', 'requesting', 'asking', 'busy', 'no_provider'].map((reason) => `ai_reason.${reason}`),
      ...kinds.map((kind) => qnaAskFailureKey(kind)),
      ...kinds.map((kind) => qnaAiFailureKey(kind)),
    ]
    for (const key of keys) expect(typeof lookup(messages, key), `${locale} ${key}`).toBe('string')
    expect(lookup(messages, 'empty')).toBeUndefined()
    // The AI refusal no longer says "the question was registered": the request is its own step.
    for (const kind of kinds) expect(String(lookup(messages, qnaAiFailureKey(kind)))).not.toMatch(/질문은 등록|was registered|質問は登録/)
  })

  it('the three locales keep the same keys', () => {
    const shape = (node: unknown): unknown =>
      node && typeof node === 'object' ? Object.fromEntries(Object.entries(node as Record<string, unknown>).map(([key, value]) => [key, shape(value)])) : 'text'
    expect(shape(qnaMessages.en)).toEqual(shape(qnaMessages.ko))
    expect(shape(qnaMessages.ja)).toEqual(shape(qnaMessages.ko))
  })
})
