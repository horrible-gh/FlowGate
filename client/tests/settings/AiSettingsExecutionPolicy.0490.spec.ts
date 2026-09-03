import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'

// 0490 T0007 §6: the "실행 정책" card on AiSettingsView — reads ai_repeat_count_max from
// GET /system/settings, saves via PATCH /system/settings with the SSOT-owning store
// (client/src/settings/stores/settings.js, §3.7), shows the server's raw 422 string verbatim,
// carries the structural min=1/max=30 bounds (§3.7), and names what it sets rather than
// repeating the screen title (§3.6).

const { getRequest, putRequest, patchRequest } = vi.hoisted(() => ({
  getRequest: vi.fn(),
  putRequest: vi.fn(),
  patchRequest: vi.fn(),
}))
vi.mock('@shared/api', () => ({ getRequest, putRequest, patchRequest }))
vi.mock('vue-router', () => ({ onBeforeRouteLeave: vi.fn() }))

const CATALOG = { exec_types: ['cli', 'api'], kinds: { cli: [], api: [] } }

function mockLoads(aiRepeatCountMax = '7') {
  getRequest.mockImplementation((url: string) => {
    if (url === '/api/v1/system/ai-settings') {
      return Promise.resolve({ data: { providers: [], default_provider_id: null, catalog: CATALOG } })
    }
    if (url === '/api/v1/system/settings') {
      return Promise.resolve({
        data: { settings: [{ setting_key: 'ai_repeat_count_max', setting_value: aiRepeatCountMax }] },
      })
    }
    return Promise.reject(new Error(`unexpected getRequest ${url}`))
  })
}

async function mountView(aiRepeatCountMax = '7') {
  setActivePinia(createPinia())
  mockLoads(aiRepeatCountMax)
  const AiSettingsView = (await import('@/settings/views/system/AiSettingsView.vue')).default
  const wrapper = mount(AiSettingsView, { global: { plugins: [i18n] } })
  await flushPromises()
  return wrapper
}

beforeEach(() => {
  getRequest.mockReset()
  putRequest.mockReset()
  patchRequest.mockReset()
})

describe('AiSettingsView execution-policy card (0490 T0007)', () => {
  it('reads the stored ceiling into the input, bounded 1..30', async () => {
    const wrapper = await mountView('7')
    const input = wrapper.get('input[type="number"][min="1"][max="30"]')
    expect((input.element as HTMLInputElement).value).toBe('7')
  })

  it('sends the ceiling as a string in the standard settings-store PATCH payload', async () => {
    patchRequest.mockResolvedValue({ data: { updated: [] } })
    const wrapper = await mountView('7')
    const input = wrapper.get('input[type="number"][min="1"][max="30"]')
    await input.setValue(15)
    await wrapper.get('[data-test="ai-execution-policy-save"]').trigger('click')
    await flushPromises()

    expect(patchRequest).toHaveBeenCalledWith('/api/v1/system/settings', {
      updates: { ai_repeat_count_max: '15' },
    })
  })

  it('shows the server 422 raw string verbatim, not the provider-row {errors:[...]} formatter', async () => {
    patchRequest.mockRejectedValue({
      response: { status: 422, data: { detail: 'ai_repeat_count_max must be an integer between 1 and 30' } },
    })
    const wrapper = await mountView('7')
    await wrapper.get('[data-test="ai-execution-policy-save"]').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('ai_repeat_count_max must be an integer between 1 and 30')
  })

  it('does not let a save failure here block the independent provider-card save button', async () => {
    patchRequest.mockRejectedValue({ response: { status: 422, data: { detail: 'boom' } } })
    const wrapper = await mountView('7')
    await wrapper.get('[data-test="ai-execution-policy-save"]').trigger('click')
    await flushPromises()

    const providerSave = wrapper.get('button.btn-primary')
    expect((providerSave.element as HTMLButtonElement).disabled).toBe(false)
  })

  it('names what the card sets rather than repeating the screen title', async () => {
    const wrapper = await mountView('7')
    const screenTitle = i18n.global.t('settings.system.ai.title')
    const cardTitle = i18n.global.t('settings.system.ai.execution_policy.title')
    const fieldLabel = i18n.global.t('settings.system.ai.execution_policy.repeat_count_max_label')
    const cardTitles = wrapper.findAll('.card-title').map((node) => node.text())
    expect(cardTitles).toContain(cardTitle)
    expect(cardTitles.some((title) => title.includes(screenTitle))).toBe(false)
    expect(wrapper.text()).toContain(fieldLabel)
  })
})
