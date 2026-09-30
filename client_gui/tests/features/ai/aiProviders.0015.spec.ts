import { mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import AiProviderEditDialog from '@/features/ai/providers/AiProviderEditDialog.vue'
import AiProvidersPanel from '@/features/ai/providers/AiProvidersPanel.vue'
import * as api from '@/features/ai/api'
import type { ProjectAiSettings, ProviderItem } from '@/features/ai/types'

registerAppMessages(i18n)

const mockSettings: ProjectAiSettings = {
  mode: 'custom',
  providers: [
    { id: 'p1', name: 'Claude Sonnet 5', exec_type: 'cli', kind: 'claude', enabled: true, cli_command: 'claude run' },
    { id: 'p2', name: 'GPT-4o', exec_type: 'api', kind: 'openai', enabled: false, api_base_url: 'https://api.openai.com/v1', api_model: 'gpt-4o' },
  ],
  default_provider_id: 'p1',
  effective: {
    source: 'project',
    providers: [
      { id: 'p1', name: 'Claude Sonnet 5', exec_type: 'cli', kind: 'claude', enabled: true, cli_command: 'claude run' },
    ],
    default_provider_id: 'p1',
    registered_count: 2,
  },
}

describe('AiProvidersPanel (0015 T0003)', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    i18n.global.locale.value = 'ko'
  })

  afterEach(() => {
    document.body.innerHTML = ''
  })

  it('renders mode options and provider table rows in custom mode', () => {
    const wrapper = mount(AiProvidersPanel, {
      props: {
        projectId: 'proj-1',
        initialSettings: mockSettings,
      },
      global: { plugins: [i18n] },
    })

    const rows = wrapper.findAll('.provider-detail-row')
    expect(rows).toHaveLength(2)
    expect(rows[0].text()).toContain('Claude Sonnet 5')
    expect(rows[1].text()).toContain('GPT-4o')

    const radios = wrapper.findAll('.radio')
    expect(radios[0].classes()).toContain('on')
    expect(radios[1].classes()).not.toContain('on')
  })

  it('filters providers by search query', async () => {
    const wrapper = mount(AiProvidersPanel, {
      props: {
        projectId: 'proj-1',
        initialSettings: mockSettings,
      },
      global: { plugins: [i18n] },
    })

    const input = wrapper.find('.detail-search input')
    await input.setValue('Sonnet')

    const rows = wrapper.findAll('.provider-detail-row')
    expect(rows).toHaveLength(1)
    expect(rows[0].text()).toContain('Claude Sonnet 5')
  })

  it('disables reordering when search filter is active and resolves by ID', async () => {
    const saveSpy = vi.spyOn(api, 'saveProjectAiSettings').mockResolvedValue(mockSettings)
    const wrapper = mount(AiProvidersPanel, {
      props: {
        projectId: 'proj-1',
        initialSettings: mockSettings,
      },
      global: { plugins: [i18n] },
    })

    // No fake repeat policy card
    expect(wrapper.find('.execution-policy').exists()).toBe(false)
    expect(wrapper.find('.repeat-val').exists()).toBe(false)

    // Unfiltered: row 0 down button can move provider p1 down
    const downButtons = wrapper.findAll('.icon-order-btn')
    expect(downButtons[1].attributes('disabled')).toBeUndefined()
    await downButtons[1].trigger('click')
    expect(saveSpy).toHaveBeenCalledWith('proj-1', expect.objectContaining({
      providers: [mockSettings.providers![1], mockSettings.providers![0]],
    }))

    // Filtered: order buttons are disabled
    const searchInput = wrapper.find('.detail-search input')
    await searchInput.setValue('GPT')
    const filteredButtons = wrapper.findAll('.icon-order-btn')
    for (const btn of filteredButtons) {
      expect(btn.attributes('disabled')).toBeDefined()
    }
  })

  it('calls saveProjectAiSettings when mode is switched', async () => {
    const saveSpy = vi.spyOn(api, 'saveProjectAiSettings').mockResolvedValue({
      ...mockSettings,
      mode: 'inherit',
    })

    const wrapper = mount(AiProvidersPanel, {
      props: {
        projectId: 'proj-1',
        initialSettings: mockSettings,
      },
      global: { plugins: [i18n] },
    })

    const inheritRadio = wrapper.find('input[value="inherit"]')
    await inheritRadio.trigger('change')

    expect(saveSpy).toHaveBeenCalledWith('proj-1', expect.objectContaining({ mode: 'inherit' }))
  })

  it('validates provider edit dialog fields', async () => {
    const wrapper = mount(AiProviderEditDialog, {
      props: {
        open: true,
        provider: null,
      },
      attachTo: document.body,
      global: { plugins: [i18n] },
    })

    const saveBtn = document.querySelector('.dialog-actions .ui-btn--primary') as HTMLButtonElement
    expect(saveBtn).toBeTruthy()
    expect(saveBtn.disabled).toBe(true)

    const nameInput = document.querySelector('#provider-name') as HTMLInputElement
    expect(nameInput).toBeTruthy()
    nameInput.value = 'New Test Provider'
    nameInput.dispatchEvent(new Event('input'))
    await wrapper.vm.$nextTick()

    expect(saveBtn.disabled).toBe(false)
    saveBtn.click()

    expect(wrapper.emitted('save')?.[0][0]).toMatchObject({
      name: 'New Test Provider',
      exec_type: 'cli',
    })
    wrapper.unmount()
  })

  it('renders disabled hint explicitly when mode is disabled', () => {
    const disabledSettings: ProjectAiSettings = {
      ...mockSettings,
      mode: 'disabled',
    }

    const wrapper = mount(AiProvidersPanel, {
      props: {
        projectId: 'proj-1',
        initialSettings: disabledSettings,
      },
      global: { plugins: [i18n] },
    })

    expect(wrapper.find('.detail-hint').text()).toBe('현재 프로젝트에서 AI 기능이 비활성화되어 있습니다.')
    expect(wrapper.find('.detail-hint').text()).not.toContain('즉시 반영됩니다')
  })

  it('handles transition from inherit to custom without implying key was copied and surfaces credential setup requirement', async () => {
    const inheritSettings: ProjectAiSettings = {
      mode: 'inherit',
      providers: null,
      default_provider_id: 'sys-api',
      effective: {
        source: 'system',
        providers: [
          {
            id: 'sys-api',
            name: 'System OpenAI',
            exec_type: 'api',
            kind: 'openai',
            enabled: true,
            api_base_url: 'https://api.openai.com/v1',
            api_model: 'gpt-4o',
            api_key_set: true,
            api_key_hint: '9999',
          },
        ],
        default_provider_id: 'sys-api',
        registered_count: 1,
      },
    }

    const customSavedSettings: ProjectAiSettings = {
      mode: 'custom',
      providers: [
        {
          id: 'proj-api-1',
          name: 'System OpenAI',
          exec_type: 'api',
          kind: 'openai',
          enabled: true,
          api_base_url: 'https://api.openai.com/v1',
          api_model: 'gpt-4o',
          api_key_set: false,
          api_key_hint: null,
        },
      ],
      default_provider_id: 'proj-api-1',
      effective: {
        source: 'project',
        providers: [
          {
            id: 'proj-api-1',
            name: 'System OpenAI',
            exec_type: 'api',
            kind: 'openai',
            enabled: true,
            api_base_url: 'https://api.openai.com/v1',
            api_model: 'gpt-4o',
            api_key_set: false,
            api_key_hint: null,
          },
        ],
        default_provider_id: 'proj-api-1',
        registered_count: 1,
      },
    }

    const saveSpy = vi.spyOn(api, 'saveProjectAiSettings').mockResolvedValue(customSavedSettings)

    const wrapper = mount(AiProvidersPanel, {
      props: {
        projectId: 'proj-1',
        initialSettings: inheritSettings,
      },
      global: { plugins: [i18n] },
    })

    const customRadio = wrapper.find('input[value="custom"]')
    await customRadio.trigger('change')

    // Transition payload should NOT imply key was copied
    expect(saveSpy).toHaveBeenCalledWith('proj-1', expect.objectContaining({
      mode: 'custom',
      providers: [
        expect.objectContaining({
          id: 'sys-api',
          name: 'System OpenAI',
          exec_type: 'api',
          api_key_set: false,
          api_key_hint: null,
        }),
      ],
    }))

    // Wait for saved settings update
    await wrapper.setProps({ initialSettings: customSavedSettings })

    // Surface credential setup requirement
    expect(wrapper.find('.panel-warning-alert').exists()).toBe(true)
    expect(wrapper.find('.panel-warning-alert').text()).toContain('API 키가 설정되지 않았습니다')
    expect(wrapper.find('.credential-badge').exists()).toBe(true)
    expect(wrapper.find('.credential-badge').text()).toContain('API 키 설정 필요')
  })
})