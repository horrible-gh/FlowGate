// flowgate.v02.0014 R0001 HOLD 2.4: AI settings are split into Overview / Provider /
// Evaluation / AI Group tabs that follow the project Settings tab convention (.tab-nav,
// route query, only the active tab mounted). Overview is a status summary with no write
// control; its only actions navigate to the Provider / Evaluation / AI Group tabs.
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'
import i18n from '@shared/i18n'
import { resetDialogSystem } from '@main/composables/useDialogStack'
import { useSettingsStore } from '@/settings/stores/settings.js'
import AiProjectSettingsView from '@/settings/views/project/AiProjectSettingsView.vue'

const { getRequest, postRequest, putRequest, deleteRequest } = vi.hoisted(() => ({
  getRequest: vi.fn(), postRequest: vi.fn(), putRequest: vi.fn(), deleteRequest: vi.fn(),
}))
vi.mock('@shared/api', () => ({ getRequest, postRequest, putRequest, deleteRequest }))

const CATALOG = { exec_types: ['cli', 'api'], kinds: { cli: ['claude'], api: ['claude'] } }
const PROVIDER = { id: 'aip_1', name: 'P1', exec_type: 'cli', kind: 'claude', enabled: true, cli_command: 'claude -p -' }

const wrappers: VueWrapper[] = []
let router: Router

async function mountView(query: Record<string, string> = { tab: 'ai' }) {
  const pinia = createPinia()
  setActivePinia(pinia)
  useSettingsStore().currentProjectId = 'proj_001'
  router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/settings/project', component: { render: () => null } }] })
  await router.push({ path: '/settings/project', query })
  await router.isReady()
  const wrapper = mount(AiProjectSettingsView, { global: { plugins: [pinia, i18n, router] } })
  wrappers.push(wrapper)
  await flushPromises()
  return wrapper
}
const has = (wrapper: VueWrapper, selector: string) => wrapper.find(selector).exists()

beforeEach(() => {
  getRequest.mockImplementation((url: string) => {
    if (url.endsWith('/ai-settings')) {
      return Promise.resolve({ data: {
        mode: 'custom', providers: [PROVIDER], default_provider_id: 'aip_1',
        effective: { source: 'project', providers: [{ id: 'aip_1', name: 'P1' }], default_provider_id: 'aip_1' },
        catalog: CATALOG,
      } })
    }
    if (url.endsWith('/ai-overview')) {
      return Promise.resolve({ data: {
        providers: { enabled: 1, total: 1 },
        evaluation: { providers_with_current_result: 0, running: 0, failed: 0, stale: 0 },
        ai_groups: { total: 0, invalid: 0 },
      } })
    }
    return Promise.resolve({ data: { items: [], next_cursor: null, current: null, latest_history: null } })
  })
})

afterEach(() => {
  while (wrappers.length) wrappers.pop()?.unmount()
  vi.clearAllMocks()
  resetDialogSystem()
  document.body.innerHTML = ''
})

describe('AI settings tabs (HOLD 2.4)', () => {
  it('renders the four sub-tabs with the Settings .tab-nav markup and starts on Overview', async () => {
    const wrapper = await mountView()
    const items = wrapper.findAll('.tab-nav .tab-nav-item')
    expect(items.map((i) => i.attributes('data-test'))).toEqual(
      ['ai-tab-overview', 'ai-tab-provider', 'ai-tab-evaluation', 'ai-tab-groups'])
    expect(items.map((i) => i.classes('active'))).toEqual([true, false, false, false])
    expect(has(wrapper, '[data-test="ai-overview"]')).toBe(true)
  })

  it('Overview has no write surface (only the three navigation buttons) and mounts none of the other tabs', async () => {
    const wrapper = await mountView()
    const overview = wrapper.get('[data-test="ai-overview"]')
    expect(overview.findAll('input, select, textarea, a')).toHaveLength(0)
    expect(overview.findAll('button').map((b) => b.attributes('data-test'))).toEqual(
      ['ai-overview-open-provider', 'ai-overview-open-evaluation', 'ai-overview-open-groups'])
    expect(overview.findAll('button').every((b) => b.attributes('type') === 'button')).toBe(true)
    expect(has(wrapper, '[data-test="ai-provider-card"]')).toBe(false)
    expect(has(wrapper, '[data-test="ai-evaluation-panel"]')).toBe(false)
    expect(has(wrapper, '[data-test="ai-groups-panel"]')).toBe(false)
    expect(putRequest).not.toHaveBeenCalled()
    expect(postRequest).not.toHaveBeenCalled()
    expect(deleteRequest).not.toHaveBeenCalled()
  })

  it.each([
    ['provider', '[data-test="ai-provider-card"]'],
    ['evaluation', '[data-test="ai-evaluation-panel"]'],
    ['groups', '[data-test="ai-groups-panel"]'],
  ])('the Overview "%s" action opens that tab and mirrors it to the ai_tab query', async (id, panel) => {
    const wrapper = await mountView()
    const button = wrapper.get(`[data-test="ai-overview-open-${id}"]`)
    expect(button.text()).toBe(i18n.global.t('settings.project.ai_v02.manage_section'))
    await button.trigger('click')
    await flushPromises()
    expect(has(wrapper, panel)).toBe(true)
    expect(has(wrapper, '[data-test="ai-overview"]')).toBe(false)
    expect(wrapper.get(`[data-test="ai-tab-${id}"]`).classes('active')).toBe(true)
    expect(router.currentRoute.value.query).toEqual({ tab: 'ai', ai_tab: id })
    expect(putRequest).not.toHaveBeenCalled()
    expect(postRequest).not.toHaveBeenCalled()
    expect(deleteRequest).not.toHaveBeenCalled()
  })

  it('the Overview actions stay available when a section summary failed to load', async () => {
    getRequest.mockImplementation((url: string) => {
      if (url.endsWith('/ai-overview')) return Promise.reject(new Error('down'))
      if (url.endsWith('/ai-settings')) {
        return Promise.resolve({ data: {
          mode: 'custom', providers: [PROVIDER], default_provider_id: 'aip_1',
          effective: { source: 'project', providers: [{ id: 'aip_1', name: 'P1' }], default_provider_id: 'aip_1' },
          catalog: CATALOG,
        } })
      }
      return Promise.resolve({ data: { items: [], next_cursor: null, current: null, latest_history: null } })
    })
    const wrapper = await mountView()
    expect(wrapper.findAll('[data-test^="ai-overview-open-"]')).toHaveLength(3)
    await wrapper.get('[data-test="ai-overview-open-groups"]').trigger('click')
    await flushPromises()
    expect(has(wrapper, '[data-test="ai-groups-panel"]')).toBe(true)
  })

  it('shows the Provider dirty/reset/save action bar only on the Provider tab', async () => {
    const wrapper = await mountView()
    expect(has(wrapper, '[data-test="ai-provider-actions"]')).toBe(false)

    await wrapper.get('[data-test="ai-tab-provider"]').trigger('click')
    await flushPromises()
    const actions = wrapper.get('[data-test="ai-provider-actions"]')
    expect(actions.findAll('button')).toHaveLength(2)

    for (const id of ['evaluation', 'groups', 'overview']) {
      await wrapper.get(`[data-test="ai-tab-${id}"]`).trigger('click')
      await flushPromises()
      expect(has(wrapper, '[data-test="ai-provider-actions"]'), id).toBe(false)
    }
  })

  it('mounts only the active tab and mirrors it to the ai_tab query next to tab=ai', async () => {
    const wrapper = await mountView()
    const panels: Record<string, string> = {
      provider: '[data-test="ai-provider-card"]',
      evaluation: '[data-test="ai-evaluation-panel"]',
      groups: '[data-test="ai-groups-panel"]',
      overview: '[data-test="ai-overview"]',
    }
    for (const id of ['provider', 'evaluation', 'groups', 'overview']) {
      await wrapper.get(`[data-test="ai-tab-${id}"]`).trigger('click')
      await flushPromises()
      for (const [other, selector] of Object.entries(panels)) expect(has(wrapper, selector), `${id} -> ${other}`).toBe(other === id)
      expect(router.currentRoute.value.query).toEqual(id === 'overview' ? { tab: 'ai' } : { tab: 'ai', ai_tab: id })
    }
  })

  it('opens directly on ?ai_tab=groups and falls back to Overview for an unknown id', async () => {
    const groups = await mountView({ tab: 'ai', ai_tab: 'groups' })
    expect(has(groups, '[data-test="ai-groups-panel"]')).toBe(true)
    expect(has(groups, '[data-test="ai-overview"]')).toBe(false)

    const unknown = await mountView({ tab: 'ai', ai_tab: 'nope' })
    expect(has(unknown, '[data-test="ai-overview"]')).toBe(true)
  })

  it('follows an external query change', async () => {
    const wrapper = await mountView()
    await router.replace({ path: '/settings/project', query: { tab: 'ai', ai_tab: 'evaluation' } })
    await flushPromises()
    expect(has(wrapper, '[data-test="ai-evaluation-panel"]')).toBe(true)
    expect(has(wrapper, '[data-test="ai-overview"]')).toBe(false)
  })

  it('keeps an unsaved Provider draft when switching tabs and back', async () => {
    const wrapper = await mountView({ tab: 'ai', ai_tab: 'provider' })
    const mode = () => wrapper.get('[data-test="ai-provider-card"] select').element as HTMLSelectElement
    expect(mode().value).toBe('custom')
    await wrapper.get('[data-test="ai-provider-card"] select').setValue('disabled')
    await wrapper.get('[data-test="ai-tab-groups"]').trigger('click')
    await flushPromises()
    expect(has(wrapper, '[data-test="ai-provider-card"]')).toBe(false)
    await wrapper.get('[data-test="ai-tab-provider"]').trigger('click')
    await flushPromises()
    expect(mode().value).toBe('disabled')
    expect(wrapper.text()).toContain(i18n.global.t('settings.ai.unsaved_badge'))
  })

  it('loads the tab data lazily: Groups/Evaluation requests only start once their tab opens', async () => {
    const wrapper = await mountView()
    const urls = () => getRequest.mock.calls.map(([url]) => String(url))
    expect(urls().some((u) => u.includes('/ai-groups'))).toBe(false)
    await wrapper.get('[data-test="ai-tab-groups"]').trigger('click')
    await flushPromises()
    expect(urls().some((u) => u.includes('/ai-groups'))).toBe(true)
  })
})
