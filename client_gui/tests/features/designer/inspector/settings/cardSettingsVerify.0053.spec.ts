/**
 * flowgate.v02test.0053 0003-T (1-A) — the two 검증 options of a work card (실행 AI's advanced ⚙): their place, the value a
 * combo entry stands for (a preset, a custom whole number inside the range; managed per card only, default 3회 / 2분 —
 * 0002-CH turn 68), the combo's options and the strings of the three locales.
 */
import { describe, expect, it } from 'vitest'
import {
  CARD_SETTINGS_FIELDS,
  CARD_SETTINGS_INVALID_REASONS,
  VERIFY_CUSTOM,
  VERIFY_FIELDS,
  VERIFY_RANGE,
  cardSettingsChoiceOptions,
  cardSettingsFailureKey,
  cardSettingsIsVerifyField,
  cardSettingsValueOf,
  cardSettingsVerifyCustomValue,
  cardSettingsVerifyOptions,
} from '@/features/designer/inspector/features/settings/cardSettingsModel'
import { AI_ADVANCED_FIELDS, AI_FIELDS, AI_GEAR_FIELDS } from '@/features/designer/inspector/features/settings/cardRolesModel'
import messages from '@/features/designer/inspector/features/settings/messages'

const RUNS_PRESETS = [3, 5, 10]
const MINUTES_PRESETS = [2, 3, 5, 10, 20, 30]

describe('검증 options — where they live', () => {
  it('a work card has both, under the 실행 AI ⚙ after the time limit; a test and a plan card have neither', () => {
    expect(VERIFY_FIELDS).toEqual(['verify_max_runs', 'verify_run_minutes'])
    expect(CARD_SETTINGS_FIELDS.run).toEqual(expect.arrayContaining([...VERIFY_FIELDS]))
    expect(CARD_SETTINGS_FIELDS.test).not.toEqual(expect.arrayContaining(['verify_max_runs']))
    expect(CARD_SETTINGS_FIELDS.plan).not.toEqual(expect.arrayContaining(['verify_max_runs']))
    expect(AI_GEAR_FIELDS.execution).toEqual(['restart', 'time_limit', 'verify_max_runs', 'verify_run_minutes'])
    expect(AI_ADVANCED_FIELDS.run).toEqual(expect.arrayContaining([...VERIFY_FIELDS]))
    expect(AI_FIELDS.run).toEqual(expect.arrayContaining([...VERIFY_FIELDS]))
    expect(AI_FIELDS.test as readonly string[]).not.toContain('verify_max_runs')
    expect(AI_FIELDS.plan as readonly string[]).not.toContain('verify_run_minutes')
    expect(cardSettingsIsVerifyField('verify_max_runs')).toBe(true)
    expect(cardSettingsIsVerifyField('time_limit')).toBe(false)
  })
})

describe('검증 options — the value a combo entry stands for', () => {
  it.each([
    ['verify_max_runs', '', undefined],          // no blank entry any more: nothing is sent
    ['verify_run_minutes', '', undefined],
    ['verify_run_minutes', '2', 2],
    ['verify_max_runs', '5', 5],
    ['verify_max_runs', '7', 7],
    ['verify_max_runs', '999', 999],
    ['verify_max_runs', '1000', 1000],
    ['verify_max_runs', '100000', 100000],
    ['verify_run_minutes', '1', 1],
    ['verify_run_minutes', '30', 30],
    ['verify_max_runs', '0', undefined],
    ['verify_run_minutes', '0', undefined],
    ['verify_run_minutes', '31', undefined],
    ['verify_run_minutes', '2.5', undefined],
    ['verify_run_minutes', '-3', undefined],
    ['verify_max_runs', 'abc', undefined],
    ['verify_max_runs', VERIFY_CUSTOM, undefined],
  ] as const)('%s “%s” is sent as %s', (field, text, expected) => {
    expect(cardSettingsValueOf(field, text)).toBe(expected)
  })

  it('a custom entry is a whole number inside the range, spaces around it allowed', () => {
    expect(cardSettingsVerifyCustomValue('verify_run_minutes', ' 12 ')).toBe(12)
    expect(cardSettingsVerifyCustomValue('verify_run_minutes', '31')).toBeUndefined()
    expect(cardSettingsVerifyCustomValue('verify_max_runs', '1e2')).toBeUndefined()
    expect(cardSettingsVerifyCustomValue('verify_max_runs', '')).toBeUndefined()
    expect(cardSettingsVerifyCustomValue('verify_max_runs', '1000')).toBe(1000)   // 0003-T: 'a whole number of 1 or more' — no upper bound
    expect(cardSettingsVerifyCustomValue('verify_max_runs', '0')).toBeUndefined()
    expect(cardSettingsVerifyCustomValue('verify_max_runs', '99999999999999999999')).toBeUndefined()   // beyond a safe integer
    expect(VERIFY_RANGE).toEqual({ verify_max_runs: [1, null], verify_run_minutes: [1, 30] })
  })
})

describe('검증 options — the combo', () => {
  it('the server presets (no system default entry), then 사용자 정의', () => {
    expect(cardSettingsVerifyOptions('verify_max_runs', RUNS_PRESETS, 3).map((o) => [o.value, o.key, o.params])).toEqual([
      ['3', 'count.times', { n: 3 }],
      ['5', 'count.times', { n: 5 }],
      ['10', 'count.times', { n: 10 }],
      [VERIFY_CUSTOM, 'option.custom', {}],
    ])
    expect(cardSettingsVerifyOptions('verify_run_minutes', MINUTES_PRESETS, 2).map((o) => o.value)).toEqual(['2', '3', '5', '10', '20', '30', VERIFY_CUSTOM])
    expect(cardSettingsVerifyOptions('verify_run_minutes', MINUTES_PRESETS, 10).every((o) => o.key !== null)).toBe(true)
  })

  it('a stored custom value stays shown, in order, so opening a card changes nothing', () => {
    expect(cardSettingsVerifyOptions('verify_max_runs', RUNS_PRESETS, 7).map((o) => o.value)).toEqual(['3', '5', '7', '10', VERIFY_CUSTOM])
    expect(cardSettingsVerifyOptions('verify_run_minutes', MINUTES_PRESETS, 17).map((o) => o.value)).toEqual(['2', '3', '5', '10', '17', '20', '30', VERIFY_CUSTOM])
    expect(cardSettingsVerifyOptions('verify_max_runs', RUNS_PRESETS, 5).map((o) => o.value)).toEqual(['3', '5', '10', VERIFY_CUSTOM])
    expect(cardSettingsChoiceOptions('verify_run_minutes', MINUTES_PRESETS, 17).map((o) => o.value)).toContain('17')
  })

  it('a server that has no presets yet still offers 사용자 정의', () => {
    expect(cardSettingsVerifyOptions('verify_max_runs', null, null).map((o) => o.value)).toEqual([VERIFY_CUSTOM])
  })
})

describe('검증 options — strings', () => {
  it('a refused value says it is out of range', () => {
    expect(CARD_SETTINGS_INVALID_REASONS).toContain('out_of_range')
    expect(cardSettingsFailureKey('invalid', 'out_of_range')).toBe('card_failure.invalid_reason.out_of_range')
  })

  it.each(['ko', 'en', 'ja'] as const)('%s has the entries, the hints and the refusal sentence', (locale) => {
    const m = messages[locale] as Record<string, any>
    expect(m.option.system_default).toBeUndefined()
    expect(m.option.custom).toBeTruthy()
    expect(m.verify.max_runs_hint).toBeTruthy()
    expect(m.verify.run_minutes_hint).toBeTruthy()
    expect(m.card_failure.invalid_reason.out_of_range).toBeTruthy()
    expect(m.count.times).toContain('{n}')
  })
})
