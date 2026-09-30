import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createRouter, createWebHistory } from 'vue-router'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import AiSettingsView from '@/features/ai/AiSettingsView.vue'
import { useProjectStore } from '@/features/project'
import * as api from '@/features/ai/api'

registerAppMessages(i18n)

const router = createRouter({
  history: createWebHistory('/app'),
  routes: [{ path: '/ai/:rest(.*)*', name: 'ai', component: AiSettingsView }],
})

describe('AiSettingsView (0015 T0003)', () => {
  beforeEach(async () => {
    setActivePinia(createPinia())
    vi.restoreAllMocks()
    i18n.global.locale.value = 'ko'
    await router.push('/ai')
    await router.isReady()
  })

  it('renders empty state when no project is active', () => {
    const projectStore = useProjectStore()
    projectStore.projects = []
    projectStore.setCurrentProject({ id: '', name: '', description: '', color: null, isActive: false, createdAt: null, updatedAt: null, modules: [] })

    const wrapper = mount(AiSettingsView, {
      global: { plugins: [i18n, router] },
    })

    expect(wrapper.find('.no-project-box').exists()).toBe(true)
    expect(wrapper.find('.no-project-box').text()).toContain('선택된 프로젝트가 없습니다')
  })

  it('loads and renders tabs and active panel when project is selected', async () => {
    const projectStore = useProjectStore()
    projectStore.projects = [
      { id: 'proj-1', name: 'Test Proj', description: '', color: null, isActive: true, createdAt: null, updatedAt: null, modules: [] },
    ]
    projectStore.setCurrentProject(projectStore.projects[0])

    vi.spyOn(api, 'fetchAiOverview').mockResolvedValue({
      providers: { mode: 'inherit', total: 1, enabled: 1, default_provider_id: 'p1' },
      evaluation: { providers_with_current_result: 1, running: 0, failed: 0, stale: 0 },
      ai_groups: { total: 1, invalid: 0 },
      errors: [],
    })
    vi.spyOn(api, 'fetchProjectAiSettings').mockResolvedValue({
      mode: 'inherit',
      providers: null,
      default_provider_id: 'p1',
      effective: { source: 'system', providers: [{ id: 'p1', name: 'Claude', exec_type: 'cli', kind: 'claude', enabled: true }], default_provider_id: 'p1', registered_count: 1 },
    })
    vi.spyOn(api, 'fetchEvaluationHistory').mockResolvedValue({ items: [], next_cursor: null })
    vi.spyOn(api, 'listAiGroups').mockResolvedValue([])

    const wrapper = mount(AiSettingsView, {
      global: { plugins: [i18n, router] },
    })

    await new Promise((r) => setTimeout(r, 20))

    expect(wrapper.find('.view-header').exists()).toBe(true)
    const tabs = wrapper.findAll('.ui-tab')
    expect(tabs.map((t) => t.text())).toEqual(['개요', 'Provider 관리', 'AI 평가', 'AI 그룹'])

    // Default tab is overview
    expect(wrapper.findComponent({ name: 'AiOverviewPanel' }).exists()).toBe(true)

    // Switch to Providers tab
    await tabs[1].trigger('click')
    expect(wrapper.findComponent({ name: 'AiProvidersPanel' }).exists()).toBe(true)

    // Switch to Evaluation tab
    await tabs[2].trigger('click')
    expect(wrapper.findComponent({ name: 'AiEvaluationPanel' }).exists()).toBe(true)

    // Switch to Groups tab
    await tabs[3].trigger('click')
    expect(wrapper.findComponent({ name: 'AiGroupsPanel' }).exists()).toBe(true)
  })

  it('handles per-section errors and offers retry on failed section tab instead of blank/empty screen', async () => {
    const projectStore = useProjectStore()
    projectStore.projects = [
      { id: 'proj-1', name: 'Test Proj', description: '', color: null, isActive: true, createdAt: null, updatedAt: null, modules: [] },
    ]
    projectStore.setCurrentProject(projectStore.projects[0])

    vi.spyOn(api, 'fetchAiOverview').mockResolvedValue({
      providers: { mode: 'inherit', total: 1, enabled: 1, default_provider_id: 'p1' },
      evaluation: { providers_with_current_result: 1, running: 0, failed: 0, stale: 0 },
      ai_groups: { total: 1, invalid: 0 },
      errors: [],
    })
    // Provider settings fails
    vi.spyOn(api, 'fetchProjectAiSettings').mockRejectedValue(new Error('Network error loading settings'))
    vi.spyOn(api, 'fetchEvaluationHistory').mockResolvedValue({ items: [], next_cursor: null })
    // Groups fails
    vi.spyOn(api, 'listAiGroups').mockRejectedValue(new Error('Internal server error loading groups'))

    const wrapper = mount(AiSettingsView, {
      global: { plugins: [i18n, router] },
    })

    await new Promise((r) => setTimeout(r, 20))

    // On Overview tab: request errors must propagate to overview cards
    const overviewPanel = wrapper.findComponent({ name: 'AiOverviewPanel' })
    expect(overviewPanel.exists()).toBe(true)
    const cardErrors = overviewPanel.findAll('.panel-error')
    expect(cardErrors).toHaveLength(2) // Providers card & Groups card failed
    expect(overviewPanel.findAll('.team-card')).toHaveLength(0)
    expect(overviewPanel.findAll('.provider-row')).toHaveLength(0)
    // Must NOT display false empty messages
    expect(overviewPanel.text()).not.toContain('등록된 AI Provider가 없습니다')
    expect(overviewPanel.text()).not.toContain('구성된 AI 그룹이 없습니다')

    const tabs = wrapper.findAll('.ui-tab')

    // Switch to Providers tab: must NOT be blank, must show error state with retry
    await tabs[1].trigger('click')
    expect(wrapper.findComponent({ name: 'AiProvidersPanel' }).exists()).toBe(false)
    expect(wrapper.find('.view-error').exists()).toBe(true)
    expect(wrapper.find('.view-error').text()).toContain('Network error loading settings')
    expect(wrapper.find('.view-error button').text()).toContain('다시 시도')

    // Switch to Groups tab: must NOT be empty list, must show error state with retry
    await tabs[3].trigger('click')
    expect(wrapper.findComponent({ name: 'AiGroupsPanel' }).exists()).toBe(false)
    expect(wrapper.find('.view-error').exists()).toBe(true)
    expect(wrapper.find('.view-error').text()).toContain('Internal server error loading groups')
    expect(wrapper.find('.view-error button').text()).toContain('다시 시도')
  })

  it('filters activeProviders to effective enabled set in custom mode', async () => {
    const projectStore = useProjectStore()
    projectStore.projects = [
      { id: 'proj-1', name: 'Test Proj', description: '', color: null, isActive: true, createdAt: null, updatedAt: null, modules: [] },
    ]
    projectStore.setCurrentProject(projectStore.projects[0])

    vi.spyOn(api, 'fetchAiOverview').mockResolvedValue({
      providers: { mode: 'custom', total: 2, enabled: 1, default_provider_id: 'p1' },
      evaluation: { providers_with_current_result: 1, running: 0, failed: 0, stale: 0 },
      ai_groups: { total: 1, invalid: 0 },
      errors: [],
    })
    vi.spyOn(api, 'fetchProjectAiSettings').mockResolvedValue({
      mode: 'custom',
      providers: [
        { id: 'p1', name: 'Enabled Sonnet', exec_type: 'cli', kind: 'claude', enabled: true },
        { id: 'p2-off', name: 'Disabled GPT', exec_type: 'api', kind: 'openai', enabled: false },
      ],
      default_provider_id: 'p1',
      effective: {
        source: 'project',
        providers: [{ id: 'p1', name: 'Enabled Sonnet', exec_type: 'cli', kind: 'claude', enabled: true }],
        default_provider_id: 'p1',
        registered_count: 2,
      },
    })
    vi.spyOn(api, 'fetchEvaluationHistory').mockResolvedValue({ items: [], next_cursor: null })
    vi.spyOn(api, 'listAiGroups').mockResolvedValue([])

    const wrapper = mount(AiSettingsView, {
      global: { plugins: [i18n, router] },
    })

    await new Promise((r) => setTimeout(r, 20))

    // Overview panel receives overviewProviders (all 2)
    const overviewComp = wrapper.findComponent({ name: 'AiOverviewPanel' })
    expect(overviewComp.props('providers')).toHaveLength(2)

    // Switch to Evaluation tab -> receives activeProviders (only 1 enabled)
    const tabs = wrapper.findAll('.ui-tab')
    await tabs[2].trigger('click')
    const evalComp = wrapper.findComponent({ name: 'AiEvaluationPanel' })
    expect(evalComp.props('providers')).toHaveLength(1)
    expect(evalComp.props('providers')[0].id).toBe('p1')

    // Switch to Groups tab -> receives activeProviders (only 1 enabled)
    await tabs[3].trigger('click')
    const groupsComp = wrapper.findComponent({ name: 'AiGroupsPanel' })
    expect(groupsComp.props('providers')).toHaveLength(1)
    expect(groupsComp.props('providers')[0].id).toBe('p1')
  })
})