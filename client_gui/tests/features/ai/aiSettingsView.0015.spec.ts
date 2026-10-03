import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createRouter, createWebHistory } from 'vue-router'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import AiSettingsView from '@/features/ai/AiSettingsView.vue'
import { useProjectStore } from '@/features/project'
import * as api from '@/features/ai/api'
import type { ProviderItem } from '@/features/ai/types'

registerAppMessages(i18n)

// AI 관리 (flowgate.v02.0017 CH0006): one screen with the AI list, evaluations and groups.
// It needs no current project; the 0015 overview / project provider tabs were retired.

const router = createRouter({
  history: createWebHistory('/app'),
  routes: [{ path: '/ai/:rest(.*)*', name: 'ai', component: AiSettingsView }],
})

const systemProviders: ProviderItem[] = [
  { id: 'p1', name: 'Enabled Sonnet', exec_type: 'cli', kind: 'claude', enabled: true },
  { id: 'p2-off', name: 'Disabled GPT', exec_type: 'api', kind: 'openai', enabled: false },
  { id: 'p3', name: 'Enabled Gemini', exec_type: 'api', kind: 'gemini', enabled: true },
]

function mockApis() {
  vi.spyOn(api, 'fetchVisibleSystemAi').mockResolvedValue({ providers: systemProviders, default_provider_id: 'p1' })
  vi.spyOn(api, 'fetchMyAi').mockResolvedValue({ providers: [], default_provider_id: null, updated_at: null })
  vi.spyOn(api, 'fetchEvaluationHistory').mockResolvedValue({ items: [], next_cursor: null })
}

async function mountView() {
  const wrapper = mount(AiSettingsView, { global: { plugins: [i18n, router] } })
  await flushPromises()
  return wrapper
}

describe('AiSettingsView (0015 T0003, AI 관리 since 0017 CH0006)', () => {
  beforeEach(async () => {
    setActivePinia(createPinia())
    vi.restoreAllMocks()
    i18n.global.locale.value = 'ko'
    await router.push('/ai')
    await router.isReady()
  })

  afterEach(() => {
    document.body.innerHTML = ''
  })

  it('opens on the AI list with three tabs and needs no current project', async () => {
    mockApis()
    const listGroups = vi.spyOn(api, 'listAiGroups').mockResolvedValue([])
    expect(useProjectStore().currentProject).toBeFalsy()

    const wrapper = await mountView()

    expect(wrapper.findAll('.ui-tab').map((tab) => tab.text())).toEqual(['AI 목록', 'AI 평가', 'AI 그룹'])
    expect(wrapper.findComponent({ name: 'AiProvidersPanel' }).exists()).toBe(true)
    expect(wrapper.find('.no-project-box').exists()).toBe(false)
    // The AI list loads its own data; evaluation/group data waits for those tabs.
    expect(listGroups).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('switches tabs and shows the evaluation and group panels', async () => {
    mockApis()
    vi.spyOn(api, 'listAiGroups').mockResolvedValue([])
    const wrapper = await mountView()

    await wrapper.findAll('.ui-tab')[1].trigger('click')
    await flushPromises()
    expect(wrapper.findComponent({ name: 'AiEvaluationPanel' }).exists()).toBe(true)

    await wrapper.findAll('.ui-tab')[2].trigger('click')
    await flushPromises()
    expect(wrapper.findComponent({ name: 'AiGroupsPanel' }).exists()).toBe(true)
    wrapper.unmount()
  })

  it('shows an error with retry on the groups tab instead of an empty list when groups fail to load', async () => {
    mockApis()
    const listGroups = vi.spyOn(api, 'listAiGroups').mockRejectedValue(new Error('Internal server error loading groups'))
    const wrapper = await mountView()

    await wrapper.findAll('.ui-tab')[2].trigger('click')
    await flushPromises()

    expect(wrapper.findComponent({ name: 'AiGroupsPanel' }).exists()).toBe(false)
    expect(wrapper.find('.view-error').text()).toContain('Internal server error loading groups')
    const retry = wrapper.find('.view-error button')
    expect(retry.text()).toContain('다시 시도')

    listGroups.mockResolvedValue([])
    await retry.trigger('click')
    await flushPromises()
    expect(wrapper.findComponent({ name: 'AiGroupsPanel' }).exists()).toBe(true)
    wrapper.unmount()
  })

  it('passes only enabled system AI to the evaluation and group panels', async () => {
    mockApis()
    vi.spyOn(api, 'listAiGroups').mockResolvedValue([])
    const wrapper = await mountView()

    await wrapper.findAll('.ui-tab')[1].trigger('click')
    await flushPromises()
    const evalProviders = wrapper.findComponent({ name: 'AiEvaluationPanel' }).props('providers') as ProviderItem[]
    expect(evalProviders.map((p) => p.id)).toEqual(['p1', 'p3'])

    await wrapper.findAll('.ui-tab')[2].trigger('click')
    await flushPromises()
    const groupProviders = wrapper.findComponent({ name: 'AiGroupsPanel' }).props('providers') as ProviderItem[]
    expect(groupProviders.map((p) => p.id)).toEqual(['p1', 'p3'])
    wrapper.unmount()
  })
})
