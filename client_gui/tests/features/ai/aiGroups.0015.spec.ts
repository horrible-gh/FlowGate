import { mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import AiGroupsPanel from '@/features/ai/groups/AiGroupsPanel.vue'
import AiGroupEditDialog from '@/features/ai/groups/AiGroupEditDialog.vue'
import * as api from '@/features/ai/api'
import type { AiGroup, ProviderItem } from '@/features/ai/types'

registerAppMessages(i18n)

const mockProviders: ProviderItem[] = [
  { id: 'p1', name: 'Claude Sonnet 5', exec_type: 'cli', kind: 'claude', enabled: true },
  { id: 'p2', name: 'GPT-4o', exec_type: 'api', kind: 'openai', enabled: true },
]

const mockGroups: AiGroup[] = [
  {
    ai_group_id: 'g1',
    project_id: 'proj-1',
    name: '개발팀 A',
    description: '범용 개발 팀',
    work_executor: { provider_id: 'p1', provider_name: 'Claude Sonnet 5', is_available: true },
    reviewer: { provider_id: 'p2', provider_name: 'GPT-4o', is_available: true },
    reworker: { provider_id: 'p3-missing', provider_name: null, is_available: false },
  },
]

describe('AiGroupsPanel (0015 T0003)', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    i18n.global.locale.value = 'ko'
    vi.spyOn(api, 'listAiGroups').mockResolvedValue(mockGroups)
  })

  afterEach(() => {
    document.body.innerHTML = ''
  })

  it('renders group index and shows warning badge for unavailable role', () => {
    const wrapper = mount(AiGroupsPanel, {
      props: {
        projectId: 'proj-1',
        providers: mockProviders,
        initialGroups: mockGroups,
      },
      global: { plugins: [i18n] },
    })

    const items = wrapper.findAll('.group-index-item')
    expect(items).toHaveLength(1)
    expect(items[0].text()).toContain('개발팀 A')
    expect(items[0].find('.warn-dot').exists()).toBe(true)

    // Right details
    expect(wrapper.find('.group-detail-head h2').text()).toBe('개발팀 A')
    expect(wrapper.find('.supervisor-detail').text()).toContain('Claude Sonnet 5')
    expect(wrapper.findAll('.member-detail-row')[1].text()).toContain('사용 불가 Provider')
  })

  it('filters groups by search query in group index', async () => {
    const wrapper = mount(AiGroupsPanel, {
      props: {
        projectId: 'proj-1',
        providers: mockProviders,
        initialGroups: mockGroups,
      },
      global: { plugins: [i18n] },
    })

    const searchInput = wrapper.find('.group-index .detail-search input')
    await searchInput.setValue('nonexistent')
    expect(wrapper.findAll('.group-index-item')).toHaveLength(0)

    await searchInput.setValue('개발팀')
    expect(wrapper.findAll('.group-index-item')).toHaveLength(1)
  })

  it('preserves unavailable provider in edit dialog', () => {
    const wrapper = mount(AiGroupEditDialog, {
      props: {
        open: true,
        group: mockGroups[0],
        providers: mockProviders,
      },
      attachTo: document.body,
      global: { plugins: [i18n] },
    })

    const reworkerSelect = document.querySelector('#group-reworker') as HTMLSelectElement
    expect(reworkerSelect).toBeTruthy()
    const options = Array.from(reworkerSelect.querySelectorAll('option'))
    const unavailableOpt = options.find((opt) => opt.text.includes('p3-missing'))
    expect(unavailableOpt).toBeDefined()
    expect(unavailableOpt?.text).toContain('현재 사용 불가')
    wrapper.unmount()
  })

  it('deletes group after confirm dialog confirmation', async () => {
    const deleteSpy = vi.spyOn(api, 'deleteAiGroup').mockResolvedValue()

    const wrapper = mount(AiGroupsPanel, {
      props: {
        projectId: 'proj-1',
        providers: mockProviders,
        initialGroups: mockGroups,
      },
      attachTo: document.body,
      global: { plugins: [i18n] },
    })

    const deleteBtn = wrapper.find('.head-actions .ui-btn--danger')
    await deleteBtn.trigger('click')

    // Confirm dialog should be open in document.body
    const confirmBtn = document.querySelector('.ui-confirm-actions .ui-btn--danger') as HTMLButtonElement
    expect(confirmBtn).toBeTruthy()
    confirmBtn.click()

    expect(deleteSpy).toHaveBeenCalledWith('proj-1', 'g1')
    wrapper.unmount()
  })

  it('filters out disabled providers from selectable options while displaying existing broken role as unavailable', () => {
    const mixedProviders: ProviderItem[] = [
      { id: 'p1', name: 'Enabled Claude', exec_type: 'cli', kind: 'claude', enabled: true },
      { id: 'p2-off', name: 'Disabled GPT', exec_type: 'api', kind: 'openai', enabled: false },
    ]

    const groupWithDisabledRole: AiGroup = {
      ai_group_id: 'g2',
      project_id: 'proj-1',
      name: '팀 B',
      description: '비활성 Provider가 지정된 팀',
      work_executor: { provider_id: 'p2-off', provider_name: 'Disabled GPT', is_available: false },
      reviewer: null,
      reworker: null,
    }

    const wrapper = mount(AiGroupEditDialog, {
      props: {
        open: true,
        group: groupWithDisabledRole,
        providers: mixedProviders,
      },
      attachTo: document.body,
      global: { plugins: [i18n] },
    })

    // Work executor has p2-off as existing role -> displayed as unavailable
    const executorSelect = document.querySelector('#group-supervisor') as HTMLSelectElement
    expect(executorSelect).toBeTruthy()
    const execOptions = Array.from(executorSelect.querySelectorAll('option'))
    expect(execOptions.some((opt) => opt.value === 'p1')).toBe(true)
    const disabledOpt = execOptions.find((opt) => opt.value === 'p2-off')
    expect(disabledOpt).toBeDefined()
    expect(disabledOpt?.text).toContain('현재 사용 불가')

    // Reviewer (currently null) should only offer p1, never p2-off
    const reviewerSelect = document.querySelector('#group-reviewer') as HTMLSelectElement
    const revOptions = Array.from(reviewerSelect.querySelectorAll('option'))
    expect(revOptions.some((opt) => opt.value === 'p2-off')).toBe(false)
    expect(revOptions.some((opt) => opt.value === 'p1')).toBe(true)

    wrapper.unmount()
  })
})