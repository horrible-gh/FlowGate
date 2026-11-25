import { describe, expect, it, vi } from 'vitest'
import {
  TITLE_SEPARATOR,
  isMyAiPreferences,
  titleChoices,
  useMyAiPreferences,
  type MyAiPreferencesShape,
} from '@shared/myAiPreferences'

// flowgate.v02test.0096 F96-5: the one load / save logic of my repeat limit and title AI, shared by
// AI 관리 › 기타 설정 (내 값) and the ⚙ settings window's AI tab.
const base = (over: Partial<MyAiPreferencesShape> = {}): MyAiPreferencesShape => ({
  title_provider: 'auto',
  title_provider_effective: 'sys_a',
  repeat_count_max: null,
  repeat_count_max_effective: 5,
  repeat_count_system_max: 5,
  repeat_count_min: 1,
  ...over,
})

const flush = () => new Promise((resolve) => setTimeout(resolve, 0))

describe('shared myAiPreferences (v02test.0096 F96-5)', () => {
  it('checks the contract shape', () => {
    expect(isMyAiPreferences(base())).toBe(true)
    expect(isMyAiPreferences({ title_provider: 'auto' })).toBe(false)
    expect(isMyAiPreferences(null)).toBe(false)
  })

  it('loads, normalizes a missing repeat_count_max and marks a bad body as an error', async () => {
    const ok = useMyAiPreferences({ fetch: async () => ({ ...base(), repeat_count_max: undefined as unknown as null }), save: vi.fn() })
    await ok.load()
    expect(ok.state.value).toBe('ready')
    expect(ok.prefs.value?.repeat_count_max).toBeNull()

    const bad = useMyAiPreferences({ fetch: async () => ({}) as MyAiPreferencesShape, save: vi.fn() })
    await bad.load()
    expect(bad.state.value).toBe('error')
    expect(bad.prefs.value).toBeNull()
  })

  it('steps within min ~ system max, resets to the system value and saves a title choice', async () => {
    const save = vi.fn(async () => base({ repeat_count_max: 4, repeat_count_max_effective: 4 }))
    const c = useMyAiPreferences({ fetch: async () => base(), save })
    await c.load()
    c.stepRepeat(1) // 6 > system max 5: refused
    expect(save).not.toHaveBeenCalled()
    c.stepRepeat(-1)
    expect(c.prefs.value?.repeat_count_max_effective).toBe(4) // shown at once
    expect(save).toHaveBeenLastCalledWith({ repeat_count_max: 4 })
    await flush()
    c.resetRepeat()
    expect(save).toHaveBeenLastCalledWith({ repeat_count_max: null })
    expect(c.prefs.value?.repeat_count_max_effective).toBe(5)
    await flush()
    c.chooseTitle(TITLE_SEPARATOR)
    expect(save).toHaveBeenCalledTimes(2)
  })

  it('puts the previous value back and reports a failed save', async () => {
    const onSaveError = vi.fn()
    const c = useMyAiPreferences({
      fetch: async () => base(),
      save: async () => { throw new Error('boom') },
      onSaveError,
    })
    await c.load()
    c.chooseTitle('off')
    expect(c.prefs.value?.title_provider).toBe('off')
    await flush()
    expect(c.prefs.value?.title_provider).toBe('auto')
    expect(onSaveError).toHaveBeenCalledTimes(1)
    expect(c.saving.value).toBe(false)
  })

  it('builds title rows: fixed / separator / AI, and always offers the stored value', () => {
    const fixed = [{ value: 'off', label: 'Off' }]
    expect(titleChoices('off', fixed, [])).toEqual(fixed)
    const rows = titleChoices('gone_ai', fixed, [{ value: 'a', label: 'A' }])
    expect(rows.map((r) => r.value)).toEqual(['off', TITLE_SEPARATOR, 'a', 'gone_ai'])
    expect(rows[1].disabled).toBe(true)
  })
})
