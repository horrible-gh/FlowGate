// flowgate.v02test.0096 ④ — the ⚙ settings window: modeless, 대화 / 음성 / AI tabs (horizontal by default, vertical by
// one prop), last place / size / tab remembered, always inside the viewport, and the AI tab's saves.
// F96-8: the height follows the content unless resized by hand, and the 대화 tab saves each change at once.
import { flushPromises, mount } from '@vue/test-utils'
import { reactive } from 'vue'
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

  it('AI tab: 고정한 AI / 채팅 최대 시간 / 제목 짓기 AI and 📌 pins; no 이 대화의 AI or 최대 반복 (F108-6)', async () => {
    const w = mountSettings()
    await w.get('[data-test="conv-settings-btn"]').trigger('click')
    await w.findAll('[role="tab"]')[2].trigger('click')
    await flushPromises()
    const ai = w.get('[data-test="conv-ai-settings"]')
    expect(ai.findAll('.cas-label, .csx-label').map((l) => l.text())).toEqual(['고정한 AI', '채팅 최대 시간', '제목 짓기 AI'])
    expect(w.find('[data-test="conv-setting-ai"]').exists()).toBe(false)
    expect(w.find('[data-test="conv-setting-repeat-decrease"]').exists()).toBe(false)
    const text = w.get('[data-test="conv-settings"]').text()
    for (const gone of ['이 대화의 AI', '최대 반복', '타임아웃', '시스템']) expect(text).not.toContain(gone)
    await w.get('[data-test="conv-setting-pin-add"]').setValue('p1')
    expect(w.findAll('[data-test="conv-setting-pins"] li').map((li) => li.attributes('data-provider-id'))).toEqual(['p1'])
    await w.get('[data-test="conv-setting-pin-remove"]').trigger('click')
    expect(w.find('[data-test="conv-setting-pins"]').exists()).toBe(false)
    w.unmount()
  })
})

describe('⚙ 명령 실행 제한 / 채팅 최대 시간 (v02test.0108 F108-6)', () => {
  const num = (w: ReturnType<typeof mountSettings>, key: string) => w.get(`[data-test="conv-setting-${key}-value"]`).element as HTMLInputElement
  const unit = (w: ReturnType<typeof mountSettings>, key: string) => w.get(`[data-test="conv-setting-${key}-unit"]`).element as HTMLSelectElement
  const lastPatch = (w: ReturnType<typeof mountSettings>) => {
    const saves = w.emitted('save') as [Record<string, unknown>, (ok: boolean) => void][]
    return saves[saves.length - 1][0]
  }
  async function open(w: ReturnType<typeof mountSettings>, tabIndex = 0) {
    await w.get('[data-test="conv-settings-btn"]').trigger('click')
    await w.findAll('[role="tab"]')[tabIndex].trigger('click')
    await flushPromises()
  }

  it('대화 tab: 명령 실행 제한 starts at [5][분] with 제한 없음 · 시간 · 분 · 초; the old 타임아웃 row is gone', async () => {
    const w = mountSettings()
    await open(w)
    expect(w.find('[data-test="conv-setting-timeout"]').exists()).toBe(false)
    expect(w.get('[data-test="conv-setting-command-limit-row"]').text()).toContain('명령 실행 제한')
    expect([...unit(w, 'command-limit').options].map((o) => o.text)).toEqual(['제한 없음', '시간', '분', '초'])
    expect(num(w, 'command-limit').value).toBe('5')
    expect(unit(w, 'command-limit').value).toBe('minute')
    expect(w.get('[data-test="conv-settings"]').text()).not.toContain('타임아웃')
    w.unmount()
  })

  it('AI tab: 채팅 최대 시간 starts at [—][제한 없음] with the number box disabled', async () => {
    const w = mountSettings()
    await open(w, 2)
    expect(unit(w, 'chat-limit').value).toBe('none')
    expect(num(w, 'chat-limit').disabled).toBe(true)
    expect(num(w, 'chat-limit').placeholder).toBe('—')
    expect(num(w, 'chat-limit').value).toBe('')
    w.unmount()
  })

  it('a unit pick saves the number × the new unit at once (1 from 제한 없음); 제한 없음 saves 0 / null', async () => {
    const w = mountSettings()
    await open(w)
    await w.get('[data-test="conv-setting-command-limit-unit"]').setValue('second')
    expect(lastPatch(w)).toMatchObject({ command_timeout_seconds: 5 })
    ;(w.emitted('save')!.at(-1) as [unknown, (ok: boolean) => void])[1](true)
    await w.get('[data-test="conv-setting-command-limit-unit"]').setValue('none')
    expect(lastPatch(w)).toMatchObject({ command_timeout_seconds: 0 })
    expect(num(w, 'command-limit').disabled).toBe(true)
    ;(w.emitted('save')!.at(-1) as [unknown, (ok: boolean) => void])[1](true)
    await w.findAll('[role="tab"]')[2].trigger('click')
    await flushPromises()
    await w.get('[data-test="conv-setting-chat-limit-unit"]').setValue('hour')
    expect(lastPatch(w)).toMatchObject({ timeout_seconds: 3600 })
    expect(num(w, 'chat-limit').value).toBe('1')
    w.unmount()
  })

  it('a typed number saves on Enter, out-of-range values are pulled to the nearest end, Esc puts the value back', async () => {
    const w = mountSettings()
    await open(w)
    const input = w.get('[data-test="conv-setting-command-limit-value"]')
    await input.setValue('90')
    await input.trigger('keydown', { key: 'Enter' })
    expect(lastPatch(w)).toMatchObject({ command_timeout_seconds: 1800 })
    expect(num(w, 'command-limit').value).toBe('30')
    expect(unit(w, 'command-limit').value).toBe('minute')
    ;(w.emitted('save')!.at(-1) as [unknown, (ok: boolean) => void])[1](false)
    await w.vm.$nextTick()
    expect(num(w, 'command-limit').value).toBe('5')
    const saves = w.emitted('save')!.length
    await input.setValue('12a')
    expect(num(w, 'command-limit').value).toBe('12')
    await input.trigger('keydown', { key: 'Escape' })
    expect(num(w, 'command-limit').value).toBe('5')
    expect(w.find('[data-test="conv-settings"]').exists()).toBe(true)
    await input.setValue('0')
    await input.trigger('blur')
    expect(w.emitted('save')!.length).toBe(saves)
    expect(num(w, 'command-limit').value).toBe('5')
    w.unmount()
  })

  it('reopens a stored value in the largest unit that divides it, and keeps both rows disabled for an unsaved chat', async () => {
    const w = mountSettings({ settings: { ...DEFAULT_CHAT_SETTINGS, commandTimeoutSeconds: 90, timeoutSeconds: 604800 } })
    await open(w)
    expect([num(w, 'command-limit').value, unit(w, 'command-limit').value]).toEqual(['90', 'second'])
    await w.findAll('[role="tab"]')[2].trigger('click')
    await flushPromises()
    expect([num(w, 'chat-limit').value, unit(w, 'chat-limit').value]).toEqual(['168', 'hour'])
    w.unmount()
    const z = mountSettings({ settings: { ...DEFAULT_CHAT_SETTINGS, commandTimeoutSeconds: 0 }, timeoutEnabled: false })
    await open(z)
    expect(unit(z, 'command-limit').value).toBe('none')
    expect(unit(z, 'command-limit').disabled).toBe(true)
    await z.findAll('[role="tab"]')[2].trigger('click')
    await flushPromises()
    expect(unit(z, 'chat-limit').disabled).toBe(true)
    z.unmount()
  })

  // Review rej_01M8H1QY7FT8KQFR
  it('a unit pick pulled back to the shown unit puts the select back ([30][분] → 시간 stays [30][분], no save)', async () => {
    const w = mountSettings({ settings: { ...DEFAULT_CHAT_SETTINGS, commandTimeoutSeconds: 1800 } })
    await open(w)
    expect([num(w, 'command-limit').value, unit(w, 'command-limit').value]).toEqual(['30', 'minute'])
    await w.get('[data-test="conv-setting-command-limit-unit"]').setValue('hour')
    await w.vm.$nextTick()
    expect(w.emitted('save')).toBeUndefined()
    expect([num(w, 'command-limit').value, unit(w, 'command-limit').value]).toEqual(['30', 'minute'])
    w.unmount()
  })

  it('a typed number too big to be exact is still pulled to the upper end', async () => {
    const w = mountSettings()
    await open(w)
    const input = w.get('[data-test="conv-setting-command-limit-value"]')
    await input.setValue('9007199254740992')
    await input.trigger('keydown', { key: 'Enter' })
    expect(lastPatch(w)).toMatchObject({ command_timeout_seconds: 1800 })
    expect([num(w, 'command-limit').value, unit(w, 'command-limit').value]).toEqual(['30', 'minute'])
    ;(w.emitted('save')!.at(-1) as [unknown, (ok: boolean) => void])[1](true)
    await input.setValue('9'.repeat(400))
    await input.trigger('blur')
    expect(lastPatch(w)).toMatchObject({ command_timeout_seconds: 1800 })
    expect(num(w, 'command-limit').value).toBe('30')
    w.unmount()
  })

  it('an invalid typed context number does not hold back 명령 실행 제한 / 채팅 최대 시간 (only the times are sent)', async () => {
    const w = mountSettings()
    await open(w)
    const settle = () => {
      const saves = (w.emitted('save') ?? []) as [unknown, (ok: boolean) => void][]
      saves.forEach(([, done]) => done(true))
    }
    await w.get('[data-test="conv-setting-context"]').setValue('custom')
    settle()
    await w.get('[data-test="conv-setting-turns"]').setValue('')
    await w.get('[data-test="conv-setting-turns"]').trigger('change')
    expect(w.find('[data-test="conv-setting-turns-error"]').exists()).toBe(true)
    const before = (w.emitted('save') ?? []).length
    await w.get('[data-test="conv-setting-command-limit-unit"]').setValue('second')
    expect(w.emitted('save')!.length).toBe(before + 1)
    expect(lastPatch(w)).toEqual({ command_timeout_seconds: 5 })
    settle()
    await w.findAll('[role="tab"]')[2].trigger('click')
    await flushPromises()
    await w.get('[data-test="conv-setting-chat-limit-unit"]').setValue('hour')
    expect(w.emitted('save')!.length).toBe(before + 2)
    expect(lastPatch(w)).toMatchObject({ timeout_seconds: 3600 })
    expect('send_action' in lastPatch(w)).toBe(false)
    expect('context_turns' in lastPatch(w)).toBe(false)
    w.unmount()
  })

  it('a failed save redraws the confirmed settings, including a time the server already stored', async () => {
    const settings = reactive({ ...DEFAULT_CHAT_SETTINGS })
    const w = mountSettings({ settings })
    await open(w, 2)
    await w.get('[data-test="conv-setting-chat-limit-unit"]').setValue('hour')
    expect(lastPatch(w)).toMatchObject({ timeout_seconds: 3600 })
    // The session kept the chat's stored time (its own PATCH passed) while /me/chat-settings failed.
    settings.timeoutSeconds = 3600
    ;(w.emitted('save')!.at(-1) as [unknown, (ok: boolean) => void])[1](false)
    await w.vm.$nextTick()
    expect([num(w, 'chat-limit').value, unit(w, 'chat-limit').value]).toEqual(['1', 'hour'])
    w.unmount()
  })
})

describe('⚙ settings › AI › 제목 짓기 AI without 자동 (F96-13)', () => {
  async function openAiTab(stored: string) {
    const api = await import('@/features/conversation/api')
    vi.mocked(api.fetchMyAiPrefs).mockResolvedValueOnce({ ...prefs, title_provider: stored, title_provider_effective: 'executor' })
    const w = mountSettings()
    await w.get('[data-test="conv-settings-btn"]').trigger('click')
    await w.findAll('[role="tab"]')[2].trigger('click')
    await flushPromises()
    return w
  }
  const options = (w: Awaited<ReturnType<typeof openAiTab>>) => w.findAll('[data-test="conv-setting-title-ai"] option')
  const selected = (w: Awaited<ReturnType<typeof openAiTab>>) =>
    (w.get('[data-test="conv-setting-title-ai"]').element as HTMLSelectElement).value

  it('offers 답한 AI 그대로 (first, the default), 끄기, a separator and the chat AI — no 자동', async () => {
    const w = await openAiTab('executor')
    expect(options(w).map((o) => o.text())).toEqual(['답한 AI 그대로', '끄기', '──────────', 'Claude Haiku 5.5 (AG)', 'Codex GPT 6.1 Sol(AG)'])
    expect(options(w).map((o) => o.attributes('value'))).not.toContain('auto')
    expect(w.get('[data-test="conv-setting-title-ai"]').text()).not.toContain('자동')
    expect(selected(w)).toBe('executor')
    w.unmount()
  })

  it('shows a stored 자동 (auto) as 답한 AI 그대로 and saves nothing for it', async () => {
    const api = await import('@/features/conversation/api')
    vi.mocked(api.saveMyAiPrefs).mockClear()
    const w = await openAiTab('auto')
    expect(selected(w)).toBe('executor')
    expect(options(w).map((o) => o.attributes('value'))).not.toContain('auto')
    expect(api.saveMyAiPrefs).not.toHaveBeenCalled()
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
