import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { nextTick } from 'vue'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import { setCurrentUser } from '@/core/auth/currentUser'
import {
  AI_FAVORITES_KEY_PREFIX,
  frequentAiIds,
  parseAiFavorites,
  resetAiFavoritesCache,
  useAiFavorites,
} from '@shared/aiFavorites'
import ComposerModelPicker from '@/features/conversation/ComposerModelPicker.vue'
import type { ChatProvider } from '@/features/conversation/types'
import AiProvidersPanel from '@/features/ai/providers/AiProvidersPanel.vue'
import * as aiApi from '@/features/ai/api'
import type { ProviderItem } from '@/features/ai/types'

// flowgate.v02test.0096 ① — the AI picker's 자주 쓰는 AI section, 📌 pins and the scroll to the selected AI.
registerAppMessages(i18n)

const USER = 'member'
const key = (owner = USER) => AI_FAVORITES_KEY_PREFIX + owner
const store = (value: { pinned?: string[]; recent?: string[] }, owner = USER) =>
  localStorage.setItem(key(owner), JSON.stringify({ pinned: value.pinned ?? [], recent: value.recent ?? [] }))

const sys = (id: string, name = id): ChatProvider => ({ id, name, owner: 'system' })
const mine = (id: string, name = id): ChatProvider => ({ id, name, owner: 'user' })

let wrapper: VueWrapper | null = null

beforeEach(() => {
  i18n.global.locale.value = 'ko'
  localStorage.clear()
  resetAiFavoritesCache()
  setCurrentUser({ username: USER, email: null, isAdmin: false })
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  setCurrentUser(null)
  vi.restoreAllMocks()
  document.body.innerHTML = ''
})

describe('frequentAiIds', () => {
  it('lists my default, then pins, then up to 3 recent, each once and only offered ids', () => {
    expect(frequentAiIds({
      defaultId: 'd',
      pinned: ['p1', 'gone', 'd', 'p2'],
      recent: ['p1', 'r1', 'r2', 'gone', 'r3', 'r4'],
      available: ['d', 'p1', 'p2', 'r1', 'r2', 'r3', 'r4'],
    })).toEqual(['d', 'p1', 'p2', 'r1', 'r2', 'r3'])
    expect(frequentAiIds({ defaultId: null, pinned: [], recent: [], available: ['a'] })).toEqual([])
  })

  it('reads a broken stored value as empty lists', () => {
    expect(parseAiFavorites('{nope')).toEqual({ pinned: [], recent: [] })
    expect(parseAiFavorites(JSON.stringify({ pinned: ['a', 'a', 3, ''], recent: 'x' }))).toEqual({ pinned: ['a'], recent: [] })
  })
})

describe('useAiFavorites', () => {
  it('stores pins and recent use per person under a key of its own', () => {
    const fav = useAiFavorites(() => USER)
    fav.togglePin('a')
    fav.togglePin('b')
    fav.togglePin('a')
    fav.recordUse('x')
    fav.recordUse('y')
    fav.recordUse('x')
    expect(JSON.parse(localStorage.getItem(key())!)).toEqual({ pinned: ['b'], recent: ['x', 'y'] })
    expect(localStorage.getItem(key('other'))).toBeNull()
    expect(useAiFavorites(() => 'other').pinned.value).toEqual([])
    // A second user of the composable (another screen) sees the same lists at once.
    expect(useAiFavorites(() => USER).isPinned('b')).toBe(true)
  })

  it('keeps the lists after a reload (read back from localStorage)', () => {
    store({ pinned: ['p'], recent: ['r'] })
    const fav = useAiFavorites(() => USER)
    expect(fav.pinned.value).toEqual(['p'])
    expect(fav.recent.value).toEqual(['r'])
  })
})

function mountPicker(props: { providers: ChatProvider[]; selectedId: string; defaultId?: string | null }) {
  wrapper = mount(ComposerModelPicker, {
    props: { status: 'ready', ...props },
    attachTo: document.body,
    global: { plugins: [i18n] },
  })
  return wrapper
}

const options = () => wrapper!.findAll('[data-test="conv-provider-option"]')
const shown = () => options().map((o) => `${o.attributes('data-section')}:${o.attributes('data-provider-id')}`)

describe('ComposerModelPicker (v02test.0096)', () => {
  it('puts 자주 쓰는 AI on top, then 내 AI before 시스템 AI, each AI once', async () => {
    store({ pinned: ['s2'], recent: ['m2', 's3', 's2'] })
    mountPicker({ providers: [sys('s1'), sys('s2'), sys('s3'), mine('m1'), mine('m2')], selectedId: 's1', defaultId: 'm1' })
    await wrapper!.get('[data-test="conv-provider-btn"]').trigger('click')
    expect(shown()).toEqual(['frequent:m1', 'frequent:s2', 'frequent:m2', 'frequent:s3', 'system:s1'])
    expect(wrapper!.find('[data-test="conv-provider-group-mine"]').exists()).toBe(false)
    expect(wrapper!.get('[data-test="conv-provider-group-frequent"]').text()).toContain('자주 쓰는 AI')
    expect(wrapper!.get('[data-test="conv-provider-group-system"]').text()).toContain('시스템 AI')
    expect(wrapper!.findAll('[data-test="conv-provider-default"]')).toHaveLength(1)
    expect(wrapper!.findAll('[data-test="conv-provider-pinned"]')).toHaveLength(1)
    expect(wrapper!.find('[role="separator"]').exists()).toBe(true)
  })

  it('without pins or recent use lists my default, then 내 AI, then 시스템 AI', async () => {
    mountPicker({ providers: [sys('s1'), sys('s2'), mine('m1'), mine('m2')], selectedId: 's2', defaultId: 's1' })
    await wrapper!.get('[data-test="conv-provider-btn"]').trigger('click')
    expect(shown()).toEqual(['frequent:s1', 'mine:m1', 'mine:m2', 'system:s2'])
    expect(options().find((o) => o.attributes('aria-selected') === 'true')!.attributes('data-provider-id')).toBe('s2')
  })

  it('scrolls the selected AI into view as soon as the popover opens', async () => {
    const scroll = vi.fn()
    Object.defineProperty(Element.prototype, 'scrollIntoView', { value: scroll, configurable: true, writable: true })
    try {
      const many = Array.from({ length: 12 }, (_, i) => sys(`s${i}`, `System ${i}`))
      mountPicker({ providers: many, selectedId: 's11', defaultId: 's0' })
      await wrapper!.get('[data-test="conv-provider-btn"]').trigger('click')
      await nextTick()
      await nextTick()
      expect(scroll).toHaveBeenCalledWith({ block: 'nearest' })
      expect((scroll.mock.contexts[0] as Element).getAttribute('data-provider-id')).toBe('s11')
    } finally {
      delete (Element.prototype as unknown as Record<string, unknown>).scrollIntoView
    }
  })

  it('records the picked AI as recently used, so it shows in 자주 쓰는 AI next time', async () => {
    mountPicker({ providers: [sys('s1'), sys('s2'), sys('s3')], selectedId: 's1', defaultId: 's1' })
    await wrapper!.get('[data-test="conv-provider-btn"]').trigger('click')
    await options().find((o) => o.attributes('data-provider-id') === 's3')!.trigger('click')
    expect(wrapper!.emitted('select')).toEqual([['s3']])
    expect(JSON.parse(localStorage.getItem(key())!).recent).toEqual(['s3'])
    await wrapper!.get('[data-test="conv-provider-btn"]').trigger('click')
    expect(shown()).toEqual(['frequent:s1', 'frequent:s3', 'system:s2'])
  })

  it('offers a name search past 8 AI, driven by ↑↓ / Enter / Esc', async () => {
    const few = Array.from({ length: 8 }, (_, i) => sys(`s${i}`))
    mountPicker({ providers: few, selectedId: 's0' })
    await wrapper!.get('[data-test="conv-provider-btn"]').trigger('click')
    expect(wrapper!.find('[data-test="conv-provider-search"]').exists()).toBe(false)
    wrapper!.unmount()

    const names = ['Claude Opus', 'Claude Sonnet', 'Codex', 'Gemini Pro', 'Gemini Flash', 'Qwen', 'Llama', 'Mistral', 'DeepSeek']
    mountPicker({ providers: names.map((n, i) => sys(`id${i}`, n)), selectedId: 'id0' })
    await wrapper!.get('[data-test="conv-provider-btn"]').trigger('click')
    await nextTick()
    const search = wrapper!.get('[data-test="conv-provider-search"]')
    expect(document.activeElement).toBe(search.element)
    await search.setValue('gem')
    expect(options().map((o) => o.text())).toEqual(['Gemini Pro', 'Gemini Flash'])
    await search.trigger('keydown', { key: 'ArrowDown' })
    await search.trigger('keydown', { key: 'Enter' })
    expect(wrapper!.emitted('select')).toEqual([['id4']])
    expect(wrapper!.find('[data-test="conv-provider-pop"]').exists()).toBe(false)

    await wrapper!.get('[data-test="conv-provider-btn"]').trigger('click')
    await nextTick()
    const again = wrapper!.get('[data-test="conv-provider-search"]')
    await again.setValue('zzz')
    expect(wrapper!.find('[data-test="conv-provider-empty"]').exists()).toBe(true)
    // Esc clears a typed search first; the popover stays open.
    await again.trigger('keydown', { key: 'Escape' })
    expect((again.element as HTMLInputElement).value).toBe('')
    expect(wrapper!.find('[data-test="conv-provider-pop"]').exists()).toBe(true)
  })
})

describe('AI list 📌 toggle (v02test.0096)', () => {
  const systemAi: ProviderItem[] = [
    { id: 'sys-a', name: 'System A', exec_type: 'cli', kind: 'claude', enabled: true, exec_location: 'remote' },
    { id: 'sys-b', name: 'System B', exec_type: 'cli', kind: 'claude', enabled: true, exec_location: 'remote' },
  ]

  it('pins an AI apart from the AI list data and the picker shows it on top at once', async () => {
    vi.spyOn(aiApi, 'fetchVisibleSystemAi').mockResolvedValue({ providers: systemAi, default_provider_id: 'sys-a' })
    vi.spyOn(aiApi, 'fetchMyAi').mockResolvedValue({ providers: [], default_provider_id: null, updated_at: null })
    vi.spyOn(aiApi, 'fetchMyAiOrder').mockResolvedValue({ order: [] })
    vi.spyOn(aiApi, 'fetchMyAiPreferences').mockRejectedValue(new Error('not needed'))
    const saveMine = vi.spyOn(aiApi, 'saveMyAi')
    const saveDefault = vi.spyOn(aiApi, 'saveMyAiDefault')
    const saveOrder = vi.spyOn(aiApi, 'saveMyAiOrder')
    const panel = mount(AiProvidersPanel, { global: { plugins: [i18n] }, attachTo: document.body })
    await flushPromises()

    const picker = mount(ComposerModelPicker, {
      props: { status: 'ready', providers: [sys('sys-a', 'System A'), sys('sys-b', 'System B'), sys('sys-c')], selectedId: 'sys-a' },
      attachTo: document.body,
      global: { plugins: [i18n] },
    })
    try {
      const pins = panel.findAll('[data-test="ai-pin-toggle"]')
      expect(pins).toHaveLength(2)
      expect(pins[1].attributes('aria-pressed')).toBe('false')
      await pins[1].trigger('click')
      expect(panel.findAll('[data-test="ai-pin-toggle"]')[1].attributes('aria-pressed')).toBe('true')
      expect(JSON.parse(localStorage.getItem(key())!).pinned).toEqual(['sys-b'])
      expect(saveMine).not.toHaveBeenCalled()
      expect(saveDefault).not.toHaveBeenCalled()
      expect(saveOrder).not.toHaveBeenCalled()

      await picker.get('[data-test="conv-provider-btn"]').trigger('click')
      const first = picker.findAll('[data-test="conv-provider-option"]')[0]
      expect(first.attributes('data-provider-id')).toBe('sys-b')
      expect(first.attributes('data-section')).toBe('frequent')

      // A reload reads the pin back.
      resetAiFavoritesCache()
      expect(useAiFavorites(() => USER).isPinned('sys-b')).toBe(true)
    } finally {
      picker.unmount()
      panel.unmount()
    }
  })
})
