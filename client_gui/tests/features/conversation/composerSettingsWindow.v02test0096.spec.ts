// flowgate.v02test.0096 ④ — the ⚙ settings window: modeless, 대화 / 음성 / AI tabs (horizontal by default, vertical by
// one prop), last place / size / tab remembered, always inside the viewport, and the AI tab's saves.
// F96-8: the height follows the content unless resized by hand, and the 대화 tab saves each change at once.
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
  repeat_count_default: 5,
  repeat_count_min: 1,
  repeat_count_hard_max: 30,
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
    expect(parseStoredWindow('{broken')).toEqual({ rect: null, tab: null, sized: false })
  })

  it('F96-8: follows the content height; an old stored height (400) is not used, only a size set by hand', async () => {
    localStorage.setItem(SETTINGS_WINDOW_KEY, JSON.stringify({ rect: { x: 20, y: 20, width: 360, height: 400 }, tab: 'chat' }))
    expect(parseStoredWindow(localStorage.getItem(SETTINGS_WINDOW_KEY)).sized).toBe(false)
    const w = mountSettings()
    await w.get('[data-test="conv-settings-btn"]').trigger('click')
    const el = () => w.get('[data-test="conv-settings"]').element as HTMLElement
    expect(el().style.height).toBe('')
    expect(parseInt(el().style.maxHeight, 10)).toBeLessThanOrEqual(window.innerHeight)

    // dragging the corner grip fixes the size, and it is remembered as set by hand
    await w.get('[data-test="conv-settings-resize"]').trigger('pointerdown', { button: 0, clientX: 100, clientY: 100 })
    document.dispatchEvent(Object.assign(new Event('pointermove'), { clientX: 120, clientY: 340 }))
    document.dispatchEvent(new Event('pointerup'))
    await w.vm.$nextTick()
    expect(el().style.height).not.toBe('')
    expect(parseStoredWindow(localStorage.getItem(SETTINGS_WINDOW_KEY)).sized).toBe(true)

    // a double-click on the grip goes back to the content height
    await w.get('[data-test="conv-settings-resize"]').trigger('dblclick')
    expect(el().style.height).toBe('')
    expect(parseStoredWindow(localStorage.getItem(SETTINGS_WINDOW_KEY)).sized).toBe(false)
    w.unmount()
  })

  it('F96-8: the 대화 tab has no 취소 / 저장 and saves a change at once; a failure puts the old value back', async () => {
    const w = mountSettings()
    await w.get('[data-test="conv-settings-btn"]').trigger('click')
    expect(w.find('[data-test="conv-settings-save"]').exists()).toBe(false)
    expect(w.text()).not.toContain('취소')
    await w.get('[data-test="conv-setting-send-action"]').setValue('copy_mention')
    const [patch, done] = w.emitted('save')![0] as [Record<string, unknown>, (ok: boolean) => void]
    expect(patch).toMatchObject({ send_action: 'copy_mention' })
    done(false)
    await w.vm.$nextTick()
    expect((w.get('[data-test="conv-setting-send-action"]').element as HTMLSelectElement).value).toBe(DEFAULT_CHAT_SETTINGS.sendAction)
    expect(w.find('[data-test="conv-settings"]').exists()).toBe(true)
    // an invalid typed number is not sent
    await w.get('[data-test="conv-setting-context"]').setValue('custom')
    await w.get('[data-test="conv-setting-turns"]').setValue('0')
    await w.get('[data-test="conv-setting-turns"]').trigger('change')
    expect(w.emitted('save')).toHaveLength(1)
    w.unmount()
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
    expect(w.find('[data-test="conv-setting-repeat-system"]').exists()).toBe(false)
    expect(w.get('[data-test="conv-settings"]').text()).not.toContain('시스템')
    await w.get('[data-test="conv-setting-repeat-decrease"]').trigger('click')
    await flushPromises()
    expect(api.saveMyAiPrefs).toHaveBeenCalledWith({ repeat_count_max: 4 })
    expect(w.get('[data-test="conv-setting-repeat-value"]').text()).toBe('4')
    w.unmount()
  })
})

describe('⚙ settings › AI › 제목 짓기 AI 자동 label (F96-10)', () => {
  async function autoLabel(effective: string): Promise<string> {
    const api = await import('@/features/conversation/api')
    vi.mocked(api.fetchMyAiPrefs).mockResolvedValueOnce({ ...prefs, title_provider_effective: effective })
    const w = mountSettings()
    await w.get('[data-test="conv-settings-btn"]').trigger('click')
    await w.findAll('[role="tab"]')[2].trigger('click')
    await flushPromises()
    const label = w.get('[data-test="conv-setting-title-ai"] option[value="auto"]').text()
    w.unmount()
    return label
  }

  it('names the AI 자동 resolves to, and shows only 자동 for an id it cannot name', async () => {
    expect(await autoLabel('executor')).toBe('자동 · 답한 AI 그대로')
    expect(await autoLabel('p2')).toBe('자동 · Codex GPT 6.1 Sol(AG)')
    expect(await autoLabel('aip_unknown_internal')).toBe('자동')
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
