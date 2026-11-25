// flowgate.v02test.0096 ④ — the ⚙ settings window: modeless, 대화 / 음성 / AI tabs (horizontal by default, vertical by
// one prop), last place / size / tab remembered, always inside the viewport, and the AI tab's saves.
import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import Tabs from '@/components/ui/Tabs.vue'
import ComposerSettings from '@/features/conversation/ComposerSettings.vue'
import { DEFAULT_CHAT_SETTINGS } from '@/features/conversation/api'
import { SETTINGS_WINDOW_KEY, clampRect, parseStoredWindow } from '@/features/conversation/floatingWindow'
import { resetAiFavoritesCache } from '@shared/aiFavorites'

const prefs = {
  title_provider: 'auto',
  title_provider_effective: 'p2',
  repeat_count_max: null,
  repeat_count_max_effective: 5,
  repeat_count_system_max: 5,
  repeat_count_min: 1,
}

vi.mock('@/features/conversation/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/features/conversation/api')>()
  return {
    ...actual,
    fetchMyAiPrefs: vi.fn(async () => ({ ...prefs })),
    saveMyAiPrefs: vi.fn(async (update: Record<string, unknown>) => ({ ...prefs, ...update, repeat_count_max_effective: (update.repeat_count_max as number | null | undefined) ?? 5 })),
  }
})

registerAppMessages(i18n)

const providers = [
  { id: 'p1', name: 'Claude Haiku 5.5 (AG)', owner: 'user' as const },
  { id: 'p2', name: 'Codex GPT 6.1 Sol(AG)', owner: 'system' as const },
]

function mountSettings(extra: Record<string, unknown> = {}) {
  return mount(ComposerSettings, {
    attachTo: document.body,
    props: { settings: { ...DEFAULT_CHAT_SETTINGS }, saving: false, hasProviders: true, providers, selectedProviderId: 'p1', ...extra },
    global: { plugins: [i18n], stubs: { ComposerVoiceSettings: true } },
  })
}

beforeEach(() => {
  localStorage.clear()
  resetAiFavoritesCache()
})
afterEach(() => {
  document.body.innerHTML = ''
})

describe('⚙ settings window (v02test.0096 ④)', () => {
  it('opens as a modeless window with 대화 / 음성 / AI tabs on top; a press elsewhere keeps it open', async () => {
    const w = mountSettings()
    await w.get('[data-test="conv-settings-btn"]').trigger('click')
    const win = w.get('[data-test="conv-settings"]')
    expect(win.attributes('aria-modal')).toBe('false')
    const tablist = w.get('[role="tablist"]')
    expect(tablist.attributes('aria-orientation')).toBe('horizontal')
    expect(w.findAll('[role="tab"]').map((b) => b.attributes('data-tab'))).toEqual(['chat', 'voice', 'ai'])
    expect(w.find('[data-test="conv-setting-context"]').exists()).toBe(true)
    document.body.dispatchEvent(new Event('pointerdown', { bubbles: true }))
    await w.vm.$nextTick()
    expect(w.find('[data-test="conv-settings"]').exists()).toBe(true)
    await w.get('[data-test="conv-settings-close"]').trigger('click')
    expect(w.find('[data-test="conv-settings"]').exists()).toBe(false)
    w.unmount()
  })

  it('switches the tab placement with one prop', async () => {
    const w = mountSettings({ tabOrientation: 'vertical' })
    await w.get('[data-test="conv-settings-btn"]').trigger('click')
    expect(w.get('[role="tablist"]').attributes('aria-orientation')).toBe('vertical')
    expect(w.get('[data-test="conv-settings"]').classes()).toContain('is-vertical')
    w.unmount()
  })

  it('remembers the last tab, place and size and restores them on reopen, pulled inside the screen', async () => {
    localStorage.setItem(SETTINGS_WINDOW_KEY, JSON.stringify({ rect: { x: 5000, y: -40, width: 320, height: 300 }, tab: null }))
    const w = mountSettings()
    await w.get('[data-test="conv-settings-btn"]').trigger('click')
    const style = (w.get('[data-test="conv-settings"]').element as HTMLElement).style
    expect(parseInt(style.left, 10) + parseInt(style.width, 10)).toBeLessThanOrEqual(window.innerWidth)
    expect(parseInt(style.top, 10)).toBeGreaterThanOrEqual(0)
    expect(style.width).toBe('320px')
    await w.findAll('[role="tab"]')[2].trigger('click')
    expect(parseStoredWindow(localStorage.getItem(SETTINGS_WINDOW_KEY)).tab).toBe('ai')
    await w.get('[data-test="conv-settings-btn"]').trigger('click')
    await w.get('[data-test="conv-settings-btn"]').trigger('click')
    expect(w.get('[role="tab"][aria-selected="true"]').attributes('data-tab')).toBe('ai')
    w.unmount()
  })

  it('clampRect keeps every edge on screen and never grows past the viewport', () => {
    expect(clampRect({ x: -50, y: 900, width: 2000, height: 300 }, { width: 800, height: 600 }, { width: 280, height: 220 })).toEqual({ x: 4, y: 296, width: 792, height: 300 })
    expect(parseStoredWindow('{broken')).toEqual({ rect: null, tab: null })
  })

  it('AI tab: picks this chat\'s AI, manages 📌 pins and saves my repeat limit at once', async () => {
    const api = await import('@/features/conversation/api')
    const w = mountSettings()
    await w.get('[data-test="conv-settings-btn"]').trigger('click')
    await w.findAll('[role="tab"]')[2].trigger('click')
    await flushPromises()
    await w.get('[data-test="conv-setting-ai"]').setValue('p2')
    expect(w.emitted('select-provider')?.[0]).toEqual(['p2'])
    await w.get('[data-test="conv-setting-pin-add"]').setValue('p1')
    expect(w.findAll('[data-test="conv-setting-pins"] li').map((li) => li.attributes('data-provider-id'))).toEqual(['p1'])
    await w.get('[data-test="conv-setting-pin-remove"]').trigger('click')
    expect(w.find('[data-test="conv-setting-pins"]').exists()).toBe(false)
    expect(w.get('[data-test="conv-setting-repeat-system"]').text()).toContain('5')
    await w.get('[data-test="conv-setting-repeat-decrease"]').trigger('click')
    await flushPromises()
    expect(api.saveMyAiPrefs).toHaveBeenCalledWith({ repeat_count_max: 4 })
    expect(w.get('[data-test="conv-setting-repeat-value"]').text()).toBe('4')
    w.unmount()
  })
})

describe('ui Tabs orientation (v02test.0096 ④)', () => {
  it('moves with ↑ ↓ when vertical and keeps ← → for the default row', async () => {
    const tabs = [{ key: 'a', label: 'A' }, { key: 'b', label: 'B' }]
    const v = mount(Tabs, { props: { tabs, modelValue: 'a', orientation: 'vertical' } })
    await v.findAll('button')[0].trigger('keydown', { key: 'ArrowRight' })
    expect(v.emitted('update:modelValue')).toBeUndefined()
    await v.findAll('button')[0].trigger('keydown', { key: 'ArrowDown' })
    expect(v.emitted('update:modelValue')?.[0]).toEqual(['b'])
    const h = mount(Tabs, { props: { tabs, modelValue: 'a' } })
    expect(h.get('[role="tablist"]').attributes('aria-orientation')).toBe('horizontal')
    await h.findAll('button')[0].trigger('keydown', { key: 'ArrowRight' })
    expect(h.emitted('update:modelValue')?.[0]).toEqual(['b'])
  })
})
