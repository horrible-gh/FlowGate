import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import * as transport from '@shared/api'
import { registerAppMessages } from '@/app/i18n'
import { setCurrentUserWithCaps as setCurrentUser } from '../../helpers/capabilities'
import AiProvidersPanel from '@/features/ai/providers/AiProvidersPanel.vue'
import * as api from '@/features/ai/api'
import type { MyAiPreferences, ProviderItem } from '@/features/ai/types'

registerAppMessages(i18n)

// v02test.0089: my own repeat limit and title AI (/me/ai-preferences) under the list of the "AI 관리 · 내 AI" window,
// for every user; on the AI management screen an administrator keeps the system values, a non-administrator sees theirs.
const systemAi: ProviderItem[] = [
  { id: 'sys-a', name: 'System A', exec_type: 'api', kind: 'claude', enabled: true, exec_location: 'local' },
  { id: 'sys-off', name: 'System off', exec_type: 'cli', kind: 'claude', enabled: false, exec_location: 'local' },
]
const myAi: ProviderItem[] = [{ id: 'my-a', name: 'My A', exec_type: 'cli', kind: 'claude', enabled: true, exec_location: 'local' }]
const policy = { repeat_count_min: 1, repeat_count_max: 5, repeat_count_hard_max: 30 }
const basePrefs: MyAiPreferences = {
  title_provider: 'auto', title_provider_effective: 'executor',
  repeat_count_max: null, repeat_count_max_effective: 3, repeat_count_system_max: 5, repeat_count_min: 1,
}

async function mountPanel({ admin = false, mineOnly = true, prefs = basePrefs as MyAiPreferences | Error } = {}) {
  setCurrentUser({ username: admin ? 'admin' : 'member', email: null, isAdmin: admin })
  if (admin) vi.spyOn(api, 'fetchSystemAi').mockResolvedValue({ providers: systemAi, default_provider_id: 'sys-a', updated_at: null, execution_policy: policy } as never)
  else vi.spyOn(api, 'fetchVisibleSystemAi').mockResolvedValue({ providers: systemAi, default_provider_id: 'sys-a' })
  vi.spyOn(api, 'fetchMyAi').mockResolvedValue({ providers: myAi, default_provider_id: null, updated_at: null })
  vi.spyOn(api, 'fetchMyAiOrder').mockResolvedValue({ order: ['my-a', 'sys-a', 'sys-off'] })
  const fetchTitle = vi.spyOn(api, 'fetchTitleProvider').mockResolvedValue('executor')
  const fetchPrefs = prefs instanceof Error
    ? vi.spyOn(api, 'fetchMyAiPreferences').mockRejectedValue(prefs)
    : vi.spyOn(api, 'fetchMyAiPreferences').mockResolvedValue({ ...prefs })
  const wrapper = mount(AiProvidersPanel, { props: { mineOnly }, global: { plugins: [i18n] }, attachTo: document.body })
  await flushPromises()
  return { wrapper, fetchTitle, fetchPrefs }
}

const value = (wrapper: ReturnType<typeof mount>) => wrapper.get('[data-test="my-repeat-value"]').text()
const selectValue = (wrapper: ReturnType<typeof mount>) =>
  (wrapper.get('[data-test="my-title-ai-select"]').element as HTMLSelectElement).value

describe('v02test.0089 내 AI window: my repeat limit and title AI', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    i18n.global.locale.value = 'ko'
  })
  afterEach(() => {
    setCurrentUser(null)
    document.body.innerHTML = ''
  })

  it('shows both cards under the list to a user who is not an administrator', async () => {
    const { wrapper, fetchTitle } = await mountPanel()
    const repeat = wrapper.get('[data-test="my-repeat-card"]')
    const title = wrapper.get('[data-test="my-title-ai-card"]')
    expect(wrapper.get('section.detail-panel').element.nextElementSibling).toBe(repeat.element)
    expect(repeat.element.nextElementSibling).toBe(title.element)
    expect(repeat.get('.repeat-info b').text()).toBe('최대 반복 횟수')
    expect(repeat.get('small').text()).toContain('5')
    expect(value(wrapper)).toBe('3')
    expect(title.get('.repeat-info b').text()).toBe('제목 짓기 AI')
    expect(selectValue(wrapper)).toBe('auto')
    expect(wrapper.find('[data-test="system-repeat-card"]').exists()).toBe(false)
    expect(wrapper.find('[data-test="title-ai-card"]').exists()).toBe(false)
    expect(fetchTitle).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('shows an administrator their own values here, not the system values', async () => {
    const { wrapper, fetchTitle } = await mountPanel({ admin: true })
    expect(wrapper.find('[data-test="my-repeat-card"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="my-title-ai-card"]').exists()).toBe(true)
    expect(wrapper.find('[data-test="system-repeat-card"]').exists()).toBe(false)
    expect(wrapper.find('[data-test="title-ai-card"]').exists()).toBe(false)
    expect(fetchTitle).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('lists 자동, 끄기, 답한 AI 그대로, a separator, then the enabled AI I can use in my order', async () => {
    const { wrapper } = await mountPanel()
    const options = wrapper.findAll('[data-test="my-title-ai-select"] option')
    expect(options.map((o) => o.text())).toEqual(['자동', '끄기', '답한 AI 그대로', '──────────', 'My A', 'System A'])
    expect(options.map((o) => (o.element as HTMLOptionElement).value).slice(0, 3)).toEqual(['auto', 'off', 'executor'])
    expect((options[3].element as HTMLOptionElement).disabled).toBe(true)
    wrapper.unmount()
  })

  it('saves a title AI choice at once and puts the previous back when the save fails', async () => {
    const save = vi.spyOn(api, 'saveMyAiPreferences').mockResolvedValue({ ...basePrefs, title_provider: 'sys-a', title_provider_effective: 'sys-a' })
    let { wrapper } = await mountPanel()
    await wrapper.get('[data-test="my-title-ai-select"]').setValue('sys-a')
    await flushPromises()
    expect(save).toHaveBeenCalledWith({ title_provider: 'sys-a' })
    expect(selectValue(wrapper)).toBe('sys-a')
    wrapper.unmount()

    save.mockRejectedValue(new Error('저장 실패'))
    ;({ wrapper } = await mountPanel())
    await wrapper.get('[data-test="my-title-ai-select"]').setValue('off')
    await flushPromises()
    expect(selectValue(wrapper)).toBe('auto')
    expect(document.body.textContent).toContain('저장 실패')
    wrapper.unmount()
  })

  it('steps my repeat limit within 1 ~ the system ceiling and saves each change at once', async () => {
    const save = vi.spyOn(api, 'saveMyAiPreferences').mockImplementation(async (update) => ({
      ...basePrefs, repeat_count_max: update.repeat_count_max ?? null, repeat_count_max_effective: update.repeat_count_max ?? 5,
    }))
    const { wrapper } = await mountPanel({ prefs: { ...basePrefs, repeat_count_max: 4, repeat_count_max_effective: 4 } })
    await wrapper.get('[data-test="my-repeat-increase"]').trigger('click')
    await flushPromises()
    expect(save).toHaveBeenLastCalledWith({ repeat_count_max: 5 })
    expect(value(wrapper)).toBe('5')
    expect((wrapper.get('[data-test="my-repeat-increase"]').element as HTMLButtonElement).disabled).toBe(true)
    await wrapper.get('[data-test="my-repeat-decrease"]').trigger('click')
    await flushPromises()
    expect(save).toHaveBeenLastCalledWith({ repeat_count_max: 4 })
    expect(value(wrapper)).toBe('4')
    wrapper.unmount()
  })

  it('goes back to the system default (null) and hides the reset while no value of my own is set', async () => {
    const save = vi.spyOn(api, 'saveMyAiPreferences').mockResolvedValue({ ...basePrefs, repeat_count_max: null, repeat_count_max_effective: 5 })
    const { wrapper } = await mountPanel({ prefs: { ...basePrefs, repeat_count_max: 2, repeat_count_max_effective: 2 } })
    const reset = wrapper.get('[data-test="my-repeat-reset"]')
    expect(reset.text()).toBe('기본값(시스템 5)으로')
    await reset.trigger('click')
    await flushPromises()
    expect(save).toHaveBeenCalledWith({ repeat_count_max: null })
    expect(value(wrapper)).toBe('5')
    expect(wrapper.find('[data-test="my-repeat-reset"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('puts the previous repeat limit back when the save is refused', async () => {
    vi.spyOn(api, 'saveMyAiPreferences').mockRejectedValue(new Error('범위 밖'))
    const { wrapper } = await mountPanel()
    await wrapper.get('[data-test="my-repeat-decrease"]').trigger('click')
    await flushPromises()
    expect(value(wrapper)).toBe('3')
    expect(document.body.textContent).toContain('범위 밖')
    wrapper.unmount()
  })

  it('keeps the cards hidden when the values cannot be read or are not in the contract shape', async () => {
    let { wrapper } = await mountPanel({ prefs: new Error('no route') })
    expect(wrapper.find('[data-test="my-repeat-card"]').exists()).toBe(false)
    expect(wrapper.find('[data-test="my-title-ai-card"]').exists()).toBe(false)
    expect(wrapper.findAll('.provider-card')).toHaveLength(1)
    wrapper.unmount()
    ;({ wrapper } = await mountPanel({ prefs: { settings: {} } as never }))
    expect(wrapper.find('[data-test="my-repeat-card"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('on the AI management screen: a non-administrator sees their own values, an administrator the system ones', async () => {
    let r = await mountPanel({ mineOnly: false })
    expect(r.wrapper.find('[data-test="my-repeat-card"]').exists()).toBe(true)
    expect(r.wrapper.find('[data-test="system-repeat-card"]').exists()).toBe(false)
    r.wrapper.unmount()
    r = await mountPanel({ mineOnly: false, admin: true })
    expect(r.fetchPrefs).not.toHaveBeenCalled()
    expect(r.wrapper.find('[data-test="my-repeat-card"]').exists()).toBe(false)
    expect(r.wrapper.get('[data-test="system-repeat-card"] .repeat-info b').text()).toBe('최대 반복 횟수 상한(시스템)')
    expect(r.wrapper.get('[data-test="title-ai-card"] .repeat-info b').text()).toBe('제목 짓기 AI 기본값(시스템)')
    r.wrapper.unmount()
  })

  it('names the new choices in English and Japanese', async () => {
    i18n.global.locale.value = 'en'
    let { wrapper } = await mountPanel()
    expect(wrapper.findAll('[data-test="my-title-ai-select"] option').map((o) => o.text()).slice(0, 3)).toEqual(['Automatic', 'Off', 'Same AI that answered'])
    wrapper.unmount()
    i18n.global.locale.value = 'ja'
    ;({ wrapper } = await mountPanel())
    expect(wrapper.findAll('[data-test="my-title-ai-select"] option').map((o) => o.text()).slice(0, 3)).toEqual(['自動', 'オフ', '回答したAIのまま'])
    wrapper.unmount()
  })
})

describe('v02test.0089 /me/ai-preferences contract', () => {
  beforeEach(() => vi.restoreAllMocks())

  it('reads GET /api/v1/me/ai-preferences', async () => {
    const get = vi.spyOn(transport, 'getRequest').mockResolvedValue({ data: basePrefs } as never)
    await expect(api.fetchMyAiPreferences()).resolves.toEqual(basePrefs)
    expect(get).toHaveBeenCalledWith('/api/v1/me/ai-preferences')
  })

  it('sends only the changed field on PATCH /api/v1/me/ai-preferences', async () => {
    const patch = vi.spyOn(transport, 'patchRequest').mockResolvedValue({ data: basePrefs } as never)
    await api.saveMyAiPreferences({ repeat_count_max: null })
    expect(patch).toHaveBeenCalledWith('/api/v1/me/ai-preferences', { repeat_count_max: null })
    await api.saveMyAiPreferences({ title_provider: 'off' })
    expect(patch).toHaveBeenLastCalledWith('/api/v1/me/ai-preferences', { title_provider: 'off' })
  })

  it.each(['flat', 'envelope'])('shows the localized reason for %s contract errors', async (shape) => {
    const refused = (code: string) => Object.assign(new Error(code), {
      isAxiosError: true, response: {
        status: 422,
        data: shape === 'flat' ? { code, ...(code === 'repeat_count_out_of_range' ? { max: 3 } : {}) } : { ok: false, error: { code } },
      },
    })
    i18n.global.locale.value = 'ko'
    vi.spyOn(transport, 'patchRequest').mockRejectedValueOnce(refused('repeat_count_out_of_range'))
    await expect(api.saveMyAiPreferences({ repeat_count_max: 99 })).rejects.toThrow(
      String(i18n.global.t('gui.ai.errors.repeat_count_out_of_range')),
    )
    vi.spyOn(transport, 'patchRequest').mockRejectedValueOnce(refused('invalid_title_provider'))
    await expect(api.saveMyAiPreferences({ title_provider: 'x' })).rejects.toThrow(
      String(i18n.global.t('gui.ai.errors.invalid_title_provider')),
    )
  })
})
