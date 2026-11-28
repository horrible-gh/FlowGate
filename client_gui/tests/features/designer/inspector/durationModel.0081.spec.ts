/** flowgate.v02test.0081 (0029-T, 시안 v4) — the pure model of the [number][unit ▾] time boxes (seconds). */
import { describe, expect, it } from 'vitest'
import {
  durationAmountOf,
  durationClamp,
  durationParts,
  durationSeconds,
  durationSplit,
  durationUnitOf,
} from '@/features/designer/inspector/parts/durationModel'
import { DURATION_DEFAULT, DURATION_RANGE, cardSettingsDurationValue } from '@/features/designer/inspector/features/settings/cardSettingsModel'

describe('durationModel', () => {
  it.each([
    [14400, 4, 'hour'],
    [5400, 90, 'minute'],
    [3600, 1, 'hour'],
    [120, 2, 'minute'],
    [60, 1, 'minute'],
    [150, 150, 'second'],
    [0, 0, 'second'],
  ] as const)('%i seconds are first shown as [%i][%s]', (seconds, amount, unit) => {
    expect(durationSplit(seconds)).toEqual({ amount, unit })
    expect(durationUnitOf(seconds)).toBe(unit)
  })

  it('keeps the unit picked while it divides the value', () => {
    expect(durationSplit(7200, 'minute')).toEqual({ amount: 120, unit: 'minute' })
    expect(durationSplit(90, 'minute')).toEqual({ amount: 90, unit: 'second' })
  })

  it('number × unit = seconds (4 시간 → 14400, 90 분 → 5400)', () => {
    expect(durationSeconds(4, 'hour')).toBe(14400)
    expect(durationSeconds(90, 'minute')).toBe(5400)
    expect(durationSeconds(45, 'second')).toBe(45)
  })

  it('takes whole numbers only — signed ones too, so a number below the range (-1) is moved to the edge, not dropped', () => {
    expect(durationAmountOf(' 12 ')).toBe(12)
    expect(durationAmountOf('-1')).toBe(-1)
    expect(durationAmountOf('+5')).toBe(5)
    expect(durationClamp(durationSeconds(-1, 'minute'), 60, 1800)).toEqual({ value: 60, clamped: 'min' })
    for (const text of ['', '2.5', '-', '--1', '-2.5', 'abc', '1e3', '99999999999999999999']) expect(durationAmountOf(text), text).toBeUndefined()
  })

  it('moves a value to the nearest edge and says which', () => {
    expect(durationClamp(240, 1800, 14400)).toEqual({ value: 1800, clamped: 'min' })
    expect(durationClamp(20000, 1800, 14400)).toEqual({ value: 14400, clamped: 'max' })
    expect(durationClamp(5400, 1800, 14400)).toEqual({ value: 5400, clamped: null })
  })

  it('names a value in parts', () => {
    expect(durationParts(5400)).toEqual([[1, 'hour'], [30, 'minute']])
    expect(durationParts(1800)).toEqual([[30, 'minute']])
    expect(durationParts(0)).toEqual([[0, 'second']])
  })
})

describe('the common contract (0029-T)', () => {
  it('ranges and defaults of the four time settings', () => {
    expect(DURATION_RANGE).toEqual({ time_limit_sec: [1800, 14400], verify_run_sec: [60, 1800], auto_case_timeout_sec: [60, 1800], ai_judge_run_sec: [60, 1800] })
    expect(DURATION_DEFAULT).toEqual({ time_limit_sec: 14400, verify_run_sec: 60, auto_case_timeout_sec: 120, ai_judge_run_sec: 120 })
  })

  it('a value sent is a whole number of seconds inside the range', () => {
    expect(cardSettingsDurationValue('time_limit_sec', '5400')).toBe(5400)
    expect(cardSettingsDurationValue('time_limit_sec', '60')).toBe(1800)
    expect(cardSettingsDurationValue('verify_run_sec', '3600')).toBe(1800)
    expect(cardSettingsDurationValue('ai_judge_run_sec', 'x')).toBeUndefined()
  })
})
