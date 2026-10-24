/**
 * flowgate.v02.0053 T#7 — cardSettingsModel as tables: a Graph card's run / test settings (T#2) — the fields
 * of each kind in v7 order, the value shown (pending over read), the value a choice stands for, the options of
 * each field (AI, repeat counts, minutes, choices), why nothing can change, and the refusal keys.
 * flowgate.v02.0056 F2-1: the progress-after-review field is gone (O1 0005-TR §4-3) and the RUN card has the
 * 지시 AI (`instruction_ai`, O2 0007-TR §4-1): its place, its options (none = the run AI), when it is shown
 * (a run card whose instruction is used) and the reasons a refused value names.
 */
import { describe, expect, it } from 'vitest'
import {
  CARD_SETTINGS_FIELDS,
  cardSettingsAiOptions,
  cardSettingsBlockedBy,
  cardSettingsChoiceOptions,
  cardSettingsCountKey,
  cardSettingsDisabledReason,
  cardSettingsErrorKey,
  cardSettingsFailureKey,
  cardSettingsInstructionAiShown,
  cardSettingsIsAiField,
  cardSettingsReviewOff,
  cardSettingsText,
  cardSettingsValue,
  cardSettingsValueOf,
  type CardSettingsFacts,
} from '@/features/designer/inspector/features/settings/cardSettingsModel'
import messages from '@/features/designer/inspector/features/settings/messages'

const NODE: CardSettingsFacts = {
  cardKind: 'run',
  locked: false,
  fields: {
    instruction: { value: 'use', choices: ['use', 'omit'] },
    run_ai: { value: null, choices: null },
    restart: { value: 0, choices: [-1, 0, 1, 2] },
    time_limit: { value: 60, choices: [30, 60, 120] },
  },
}

describe('cardSettingsModel — fields and values (flowgate.v02.0053 T#7)', () => {
  it('the fields of each kind follow v7 (T#2 fields._FIELDS)', () => {
    expect(CARD_SETTINGS_FIELDS.run).toEqual(['instruction', 'instruction_ai', 'run_ai', 'restart', 'review_ai', 'review_count', 'time_limit', 'review_basis'])
    expect(CARD_SETTINGS_FIELDS.test).toEqual(['instruction', 'run_ai', 'review_ai', 'review_count', 'result_cause', 'follow_scope'])
  })

  it('flowgate.v02.0056 F2-1: the progress-after-review field is in no kind and has no string (removed, not hidden)', () => {
    const gone = ['after', 'review'].join('_')
    for (const kind of ['run', 'test'] as const) expect(CARD_SETTINGS_FIELDS[kind] as readonly string[]).not.toContain(gone)
    for (const locale of ['ko', 'en', 'ja'] as const) expect(Object.keys(messages[locale])).not.toContain(gone)
  })

  it('flowgate.v02.0056 F2-1: the 지시 AI is a run card’s, shown only while the instruction is used', () => {
    expect(cardSettingsInstructionAiShown('run', 'use')).toBe(true)
    expect(cardSettingsInstructionAiShown('run', 'omit')).toBe(false)
    // Not read yet: drawn with the rest of the form (off); the server default is `use`.
    expect(cardSettingsInstructionAiShown('run', null)).toBe(true)
    expect(cardSettingsInstructionAiShown('test', 'use')).toBe(false)
    expect(cardSettingsIsAiField('instruction_ai')).toBe(true)
    expect(cardSettingsIsAiField('run_ai')).toBe(true)
    expect(cardSettingsIsAiField('instruction')).toBe(false)
  })

  it('the shown value: the one sent and not answered yet, else the read (stored, else default), else none', () => {
    expect(cardSettingsValue(NODE, {}, 'restart')).toBe(0)
    expect(cardSettingsValue(NODE, { restart: 2 }, 'restart')).toBe(2)
    expect(cardSettingsValue(NODE, { run_ai: null }, 'run_ai')).toBeNull()
    expect(cardSettingsValue(NODE, {}, 'review_basis')).toBeNull()
    expect(cardSettingsValue(null, {}, 'restart')).toBeNull()
  })

  it.each([
    [null, ''],
    [undefined, ''],
    [0, '0'],
    [-1, '-1'],
    ['omit', 'omit'],
    [true, 'true'],
  ])('a value as a control holds it: %o -> %o', (value, text) => {
    expect(cardSettingsText(value)).toBe(text)
  })

  it.each([
    ['run_ai', '', null],
    ['run_ai', 'aip_opus', 'aip_opus'],
    ['review_ai', '', null],
    ['restart', '-1', -1],
    ['restart', '2', 2],
    ['review_count', '0', 0],
    ['time_limit', '120', 120],
    ['time_limit', '', undefined],
    ['restart', 'x', undefined],
    ['instruction', 'omit', 'omit'],
    ['instruction_ai', '', null],
    ['instruction_ai', 'aip_sol', 'aip_sol'],
    ['review_basis', 'last', 'last'],
    ['instruction', '', undefined],
  ] as const)('%s choice %o stands for %o', (field, text, value) => {
    expect(cardSettingsValueOf(field, text)).toBe(value)
  })
})

describe('cardSettingsModel — options (flowgate.v02.0053 T#7)', () => {
  const providers = [
    { id: 'aip_opus', name: 'Claude Opus 5' },
    { id: 'aip_sol', name: 'GPT-5.6 Sol' },
  ]

  it('AI options: none first (the node’s AI / the project’s reviewer), the project’s AI, a stored AI no longer offered marked', () => {
    expect(cardSettingsAiOptions('run_ai', providers, null)).toEqual([
      { value: '', key: 'option.run_default', params: {}, label: null },
      { value: 'aip_opus', key: null, params: {}, label: 'Claude Opus 5' },
      { value: 'aip_sol', key: null, params: {}, label: 'GPT-5.6 Sol' },
    ])
    expect(cardSettingsAiOptions('review_ai', providers, 'aip_sol')[0].key).toBe('option.review_default')
    expect(cardSettingsAiOptions('review_ai', providers, 'aip_gone').at(-1)).toEqual({ value: 'aip_gone', key: 'option.missing', params: { name: 'aip_gone' }, label: null })
  })

  it('flowgate.v02.0056 F2-1: the 지시 AI options — none first is the run AI (then the assigned AI), then the project’s AI', () => {
    expect(cardSettingsAiOptions('instruction_ai', providers, null)).toEqual([
      { value: '', key: 'option.instruction_default', params: {}, label: null },
      { value: 'aip_opus', key: null, params: {}, label: 'Claude Opus 5' },
      { value: 'aip_sol', key: null, params: {}, label: 'GPT-5.6 Sol' },
    ])
    expect(cardSettingsAiOptions('instruction_ai', providers, 'aip_gone').at(-1)?.key).toBe('option.missing')
  })

  it('repeat counts: the server’s choices; restart -1 runs until done, review -1 until it passes', () => {
    expect(cardSettingsChoiceOptions('restart', [-1, 0, 1, 2], 0).map((option) => [option.value, option.key])).toEqual([
      ['-1', 'count.until_done'],
      ['0', 'count.none'],
      ['1', 'count.times'],
      ['2', 'count.times'],
    ])
    expect(cardSettingsChoiceOptions('review_count', [-1, 0, 1], 0).map((option) => option.key)).toEqual(['count.until_pass', 'count.none', 'count.times'])
    expect(cardSettingsCountKey('restart', 3)).toBe('count.times')
  })

  it('minutes and choices; a value outside the choices stays shown, as itself', () => {
    expect(cardSettingsChoiceOptions('time_limit', [30, 60], 60)).toEqual([
      { value: '30', key: 'option.minutes', params: { n: 30 }, label: null },
      { value: '60', key: 'option.minutes', params: { n: 60 }, label: null },
    ])
    expect(cardSettingsChoiceOptions('review_basis', ['type', 'last'], 'type').map((option) => option.key)).toEqual(['review_basis.type', 'review_basis.last'])
    expect(cardSettingsChoiceOptions('time_limit', [30, 60], 75).at(-1)).toEqual({ value: '75', key: null, params: {}, label: '75' })
    expect(cardSettingsChoiceOptions('restart', null, null)).toEqual([])
  })
})

describe('cardSettingsModel — availability and refusals (flowgate.v02.0053 T#7)', () => {
  const base = { readOnly: null, placing: false, read: true, locked: false, blocked: null } as const

  it.each([
    [{}, null],
    [{ readOnly: 'conflict' }, 'reason.read_only_conflict'],
    [{ readOnly: 'revision' }, 'reason.read_only_revision'],
    [{ blocked: 'disposed' }, 'reason.disposed'],
    [{ placing: true, read: false }, 'reason.placing'],
    [{ locked: true }, 'reason.locked'],
    [{ read: false }, 'reason.not_read'],
    [{ readOnly: 'revision', locked: true }, 'reason.read_only_revision'],
  ] as const)('%o -> %s', (input, reason) => {
    expect(cardSettingsDisabledReason({ ...base, ...input })).toBe(reason)
  })

  it('the review AI is not used with no review', () => {
    expect(cardSettingsReviewOff(0)).toBe(true)
    expect(cardSettingsReviewOff(-1)).toBe(false)
    expect(cardSettingsReviewOff(2)).toBe(false)
    expect(cardSettingsReviewOff(null)).toBe(false)
  })

  it.each(['locked', 'disposed', 'unavailable', 'invalid', 'forbidden', 'not_found', 'network', 'other'] as const)('refusal %s: its key; only disposed blocks', (kind) => {
    expect(cardSettingsFailureKey(kind)).toBe(`card_failure.${kind}`)
    expect(cardSettingsBlockedBy(kind)).toBe(kind === 'disposed' ? 'disposed' : null)
    expect(cardSettingsErrorKey(kind)).toBe(['forbidden', 'not_found', 'network', 'unavailable'].includes(kind) ? `card_error.${kind}` : 'card_error.other')
  })

  it.each([
    ['invalid', 'unknown_ai', 'card_failure.invalid_reason.unknown_ai'],
    ['invalid', 'not_a_text', 'card_failure.invalid_reason.not_a_text'],
    ['invalid', 'invalid_text', 'card_failure.invalid_reason.invalid_text'],
    ['invalid', 'not_a_setting_of_this_card', 'card_failure.invalid_reason.not_a_setting_of_this_card'],
    ['invalid', 'something_else', 'card_failure.invalid'],
    ['invalid', null, 'card_failure.invalid'],
    ['locked', 'unknown_ai', 'card_failure.locked'],
  ] as const)('flowgate.v02.0056 F2-1: refusal %s with reason %o -> %s', (kind, reason, key) => {
    expect(cardSettingsFailureKey(kind, reason)).toBe(key)
  })

  it('every key the rules produce has a ko / en / ja string', () => {
    const keys = [
      ...['read_only_conflict', 'read_only_revision', 'disposed', 'placing', 'locked', 'not_read', 'review_off', 'no_place', 'done'].map((key) => `reason.${key}`),
      ...['locked', 'disposed', 'unavailable', 'invalid', 'forbidden', 'not_found', 'network', 'other'].map((key) => `card_failure.${key}`),
      ...['unavailable', 'forbidden', 'not_found', 'network', 'other'].map((key) => `card_error.${key}`),
      ...['unknown_ai', 'not_a_text', 'invalid_text', 'not_a_setting_of_this_card'].map((key) => `card_failure.invalid_reason.${key}`),
      'option.instruction_default', 'instruction_ai.label', 'instruction_ai.hint',
      'option.run_default', 'option.review_default', 'option.missing', 'option.minutes', 'count.until_done', 'count.until_pass', 'count.none', 'count.times',
      'instruction.use', 'instruction.omit', 'instruction.label', 'instruction.hint',
      'review_basis.type', 'review_basis.last', 'failed',
    ]
    for (const locale of ['ko', 'en', 'ja'] as const) {
      for (const key of keys) {
        const value = key.split('.').reduce<unknown>((node, part) => (node as Record<string, unknown> | undefined)?.[part], messages[locale])
        expect(typeof value, `${locale} ${key}`).toBe('string')
      }
    }
  })

  it('the wording is tooltip length and shows no type code', () => {
    const strings = (node: unknown): string[] => (typeof node === 'string' ? [node] : Object.values(node as Record<string, unknown>).flatMap(strings))
    for (const locale of ['ko', 'en', 'ja'] as const) {
      for (const text of strings(messages[locale])) {
        expect(text.length, text).toBeLessThanOrEqual(60)
        expect(text, text).not.toMatch(/\b(WP|TSC|TSR|TS|DB)\b/)
      }
    }
  })
})
