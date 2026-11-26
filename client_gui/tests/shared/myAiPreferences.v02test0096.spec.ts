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
  repeat_count_default: 5,
  repeat_count_min: 1,
  repeat_count_hard_max: 30,
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

  it('steps above the default (no system ceiling, F96-11), resets to the default and saves a title choice', async () => {
    const save = vi.fn(async () => base({ repeat_count_max: 6, repeat_count_max_effective: 6 }))
    const c = useMyAiPreferences({ fetch: async () => base(), save })
    await c.load()
    c.stepRepeat(1) // 6 > default 5: allowed
    expect(c.prefs.value?.repeat_count_max_effective).toBe(6) // shown at once
    expect(save).toHaveBeenLastCalledWith({ repeat_count_max: 6 })
    await flush()
    c.resetRepeat()
    expect(save).toHaveBeenLastCalledWith({ repeat_count_max: null })
    expect(c.prefs.value?.repeat_count_max_effective).toBe(5)
    await flush()
    c.chooseTitle(TITLE_SEPARATOR)
    expect(save).toHaveBeenCalledTimes(2)
  })

  it('refuses a step past 30 or below 1', async () => {
    const save = vi.fn(async () => base())
    const top = useMyAiPreferences({ fetch: async () => base({ repeat_count_max: 30, repeat_count_max_effective: 30 }), save })
    await top.load()
    top.stepRepeat(1)
    const bottom = useMyAiPreferences({ fetch: async () => base({ repeat_count_max: 1, repeat_count_max_effective: 1 }), save })
    await bottom.load()
    bottom.stepRepeat(-1)
    expect(save).not.toHaveBeenCalled()
    top.stepRepeat(-1)
    expect(save).toHaveBeenLastCalledWith({ repeat_count_max: 29 })
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
    expect(c.prefs.value?.title_provider).toBe('executor') // the stored 'auto', shown as 답한 AI 그대로 (F96-13)
    expect(onSaveError).toHaveBeenCalledTimes(1)
    expect(c.saving.value).toBe(false)
  })

  it('shows and handles a stored auto as executor (F96-13: no 자동 choice); other values stay as stored', async () => {
    const save = vi.fn(async () => base({ title_provider: 'off' }))
    const c = useMyAiPreferences({ fetch: async () => base(), save })
    await c.load()
    expect(c.prefs.value?.title_provider).toBe('executor')
    c.chooseTitle('executor') // already the shown value: nothing to save
    expect(save).not.toHaveBeenCalled()
    c.chooseTitle('off')
    await flush()
    expect(save).toHaveBeenCalledWith({ title_provider: 'off' })
    expect(c.prefs.value?.title_provider).toBe('off')

    const other = useMyAiPreferences({ fetch: async () => base({ title_provider: 'sys_a' }), save: vi.fn() })
    await other.load()
    expect(other.prefs.value?.title_provider).toBe('sys_a')
  })

  it('builds title rows: fixed / separator / AI, and always offers the stored value', () => {
    const fixed = [{ value: 'off', label: 'Off' }]
    expect(titleChoices('off', fixed, [])).toEqual(fixed)
    const rows = titleChoices('gone_ai', fixed, [{ value: 'a', label: 'A' }])
    expect(rows.map((r) => r.value)).toEqual(['off', TITLE_SEPARATOR, 'a', 'gone_ai'])
    expect(rows[1].disabled).toBe(true)
  })
})
