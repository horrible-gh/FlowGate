/**
 * flowgate.v02.0053 T#6 — the Q&A rules for a person's question (0053 CH §2): asking is a change closed
 * on the answering reasons (worded for a question), then on a question or an answer on its way; a
 * question is ready when it is not blank; the refusal keys of the two stages (the question, the AI
 * request) and their wording in the three locales; no empty-list sentence is left.
 */
import { describe, expect, it } from 'vitest'
import qnaMessages from '@/features/designer/inspector/features/qna/messages'
import {
  qnaAskDisabledReason,
  qnaAskFailureKey,
  qnaAskReady,
  qnaAvailability,
  type QnaFailureKind,
  type QnaInput,
} from '@/features/designer/inspector/features/qna/qnaModel'

const base: QnaInput = { readOnly: null, groupDisposed: false, aiRunning: false, busy: false, asking: false }

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

describe('refusal keys of a question (flowgate.v02.0053 T#6)', () => {
  const kinds: QnaFailureKind[] = ['running', 'no_provider', 'disposed', 'forbidden', 'not_found', 'invalid', 'network', 'other']

  it('the question refused: one key per kind (no provider is not a question refusal)', () => {
    expect(kinds.map((kind) => qnaAskFailureKey('ask', kind))).toEqual([
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

  it('the AI request refused (the question is stored): running, no provider, network, else other', () => {
    expect(kinds.map((kind) => qnaAskFailureKey('ai', kind))).toEqual([
      'ai_failure.running',
      'ai_failure.no_provider',
      'ai_failure.other',
      'ai_failure.other',
      'ai_failure.other',
      'ai_failure.other',
      'ai_failure.network',
      'ai_failure.other',
    ])
  })

  it.each(['ko', 'en', 'ja'] as const)('every key of the question line has %s wording; no empty-list sentence', (locale) => {
    const messages = qnaMessages[locale] as Record<string, unknown>
    const keys = [
      'ask',
      'ask_placeholder',
      'failure.no_provider',
      ...['read_only_conflict', 'read_only_revision', 'disposed', 'ai_running', 'asking', 'busy'].map((reason) => `ask_reason.${reason}`),
      ...kinds.map((kind) => qnaAskFailureKey('ask', kind)),
      ...kinds.map((kind) => qnaAskFailureKey('ai', kind)),
    ]
    for (const key of keys) expect(typeof lookup(messages, key), `${locale} ${key}`).toBe('string')
    expect(lookup(messages, 'empty')).toBeUndefined()
  })

  it('the three locales keep the same keys', () => {
    const shape = (node: unknown): unknown =>
      node && typeof node === 'object' ? Object.fromEntries(Object.entries(node as Record<string, unknown>).map(([key, value]) => [key, shape(value)])) : 'text'
    expect(shape(qnaMessages.en)).toEqual(shape(qnaMessages.ko))
    expect(shape(qnaMessages.ja)).toEqual(shape(qnaMessages.ko))
  })
})
