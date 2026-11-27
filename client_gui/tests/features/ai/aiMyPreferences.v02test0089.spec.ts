import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import * as transport from '@shared/api'
import { registerAppMessages } from '@/app/i18n'
import { setCurrentUserWithCaps as setCurrentUser } from '../../helpers/capabilities'
import AiOtherSettingsPanel from '@/features/ai/manage/AiOtherSettingsPanel.vue'
import * as api from '@/features/ai/api'
import type { MyAiPreferences, ProviderItem } from '@/features/ai/types'

registerAppMessages(i18n)

// v02test.0089: my own repeat limit and title AI (/me/ai-preferences), for every user. v02test.0096 F96-3: they moved
// from under the AI list to the 기타 설정 tab; F96-6 left only the 내 값 there (the system values moved to 환경 설정).
const systemAi: ProviderItem[] = [
  { id: 'sys-a', name: 'System A', exec_type: 'api', kind: 'claude', enabled: true, exec_location: 'local' },
  { id: 'sys-off', name: 'System off', exec_type: 'cli', kind: 'claude', enabled: false, exec_location: 'local' },
]
const myAi: ProviderItem[] = [{ id: 'my-a', name: 'My A', exec_type: 'cli', kind: 'claude', enabled: true, exec_location: 'local' }]
const policy = { repeat_count_min: 1, repeat_count_max: 5, repeat_count_hard_max: 30 }
const basePrefs: MyAiPreferences = {
  title_provider: 'auto', title_provider_effective: 'executor',
  repeat_count_max: null, repeat_count_max_effective: 3, repeat_count_default: 5, repeat_count_min: 1, repeat_count_hard_max: 30,
}

async function mountPanel({ admin = false, prefs = basePrefs as MyAiPreferences | Error } = {}) {
  setCurrentUser({ username: admin ? 'admin' : 'member', email: null, isAdmin: admin })
  if (admin) vi.spyOn(api, 'fetchSystemAi').mockResolvedValue({ providers: systemAi, default_provider_id: 'sys-a', updated_at: null, execution_policy: policy } as never)
  else vi.spyOn(api, 'fetchVisibleSystemAi').mockResolvedValue({ providers: systemAi, default_provider_id: 'sys-a' })
  vi.spyOn(api, 'fetchMyAi').mockResolvedValue({ providers: myAi, default_provider_id: null, updated_at: null })
  vi.spyOn(api, 'fetchMyAiOrder').mockResolvedValue({ order: ['my-a', 'sys-a', 'sys-off'] })
  const fetchTitle = vi.spyOn(api, 'fetchTitleProvider').mockResolvedValue('executor')
  const fetchPrefs = prefs instanceof Error
    ? vi.spyOn(api, 'fetchMyAiPreferences').mockRejectedValue(prefs)
    : vi.spyOn(api, 'fetchMyAiPreferences').mockResolvedValue({ ...prefs })
  const wrapper = mount(AiOtherSettingsPanel, { global: { plugins: [i18n] }, attachTo: document.body })
  await flushPromises()
  return { wrapper, fetchTitle, fetchPrefs }
}

const value = (wrapper: ReturnType<typeof mount>) => wrapper.get('[data-test="my-repeat-value"]').text()
const selectValue = (wrapper: ReturnType<typeof mount>) =>
  (wrapper.get('[data-test="my-title-ai-select"]').element as HTMLSelectElement).value

describe('v02test.0089 my repeat limit and title AI (기타 설정 tab since v02test.0096)', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    i18n.global.locale.value = 'ko'
  })
  afterEach(() => {
    setCurrentUser(null)
    document.body.innerHTML = ''
  })

  it.each([false, true])('admin=%s: shows my values only, in one column, with no system column or its note', async (admin) => {
    const { wrapper, fetchTitle } = await mountPanel({ admin })
    const repeatRow = wrapper.get('[data-test="repeat-row"]')
    const titleRow = wrapper.get('[data-test="title-row"]')
    expect(wrapper.findAll('[role="columnheader"]')).toHaveLength(0)
    expect(repeatRow.findAll('[role="cell"]')).toHaveLength(1)
    expect(titleRow.findAll('[role="cell"]')).toHaveLength(1)
    expect(repeatRow.get('.repeat-info b').text()).toBe('최대 반복 횟수')
    expect(repeatRow.find('[data-test="my-repeat-card"]').exists()).toBe(true)
    expect(value(wrapper)).toBe('3')
    expect(titleRow.get('.repeat-info b').text()).toBe('제목 짓기 AI')
    expect(titleRow.find('[data-test="my-title-ai-card"]').exists()).toBe(true)
    expect(selectValue(wrapper)).toBe('executor') // v02test.0096 F96-13: a stored 'auto' shows as 답한 AI 그대로
    expect(wrapper.find('[data-test^="system-"]').exists()).toBe(false)
    expect(wrapper.find('[data-test="title-ai-card"]').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('시스템 (관리자)')
    expect(wrapper.text()).not.toContain('관리자가 아니면')
    expect(fetchTitle).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('lists 답한 AI 그대로 (default), 끄기, a separator, then the enabled AI I can use in my order — no 자동 (F96-13)', async () => {
    const { wrapper } = await mountPanel()
    const options = wrapper.findAll('[data-test="my-title-ai-select"] option')
    expect(options.map((o) => o.text())).toEqual(['답한 AI 그대로', '끄기', '──────────', 'My A', 'System A'])
    expect(options.map((o) => (o.element as HTMLOptionElement).value).slice(0, 2)).toEqual(['executor', 'off'])
    expect(options.map((o) => (o.element as HTMLOptionElement).value)).not.toContain('auto')
    expect((options[2].element as HTMLOptionElement).disabled).toBe(true)
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
    expect(selectValue(wrapper)).toBe('executor')
    expect(document.body.textContent).toContain('저장 실패')
    wrapper.unmount()
  })

  it('steps my repeat limit within 1 ~ 30 (no system ceiling, v02test.0096 F96-11) and saves each change at once', async () => {
    const save = vi.spyOn(api, 'saveMyAiPreferences').mockImplementation(async (update) => ({
      ...basePrefs, repeat_count_max: update.repeat_count_max ?? null, repeat_count_max_effective: update.repeat_count_max ?? 5,
    }))
    const { wrapper } = await mountPanel({ prefs: { ...basePrefs, repeat_count_max: 5, repeat_count_max_effective: 5 } })
    await wrapper.get('[data-test="my-repeat-increase"]').trigger('click')
    await flushPromises()
    expect(save).toHaveBeenLastCalledWith({ repeat_count_max: 6 }) // above the default 5
    expect(value(wrapper)).toBe('6')
    expect((wrapper.get('[data-test="my-repeat-increase"]').element as HTMLButtonElement).disabled).toBe(false)
    await wrapper.get('[data-test="my-repeat-decrease"]').trigger('click')
    await flushPromises()
    expect(save).toHaveBeenLastCalledWith({ repeat_count_max: 5 })
    expect(value(wrapper)).toBe('5')
    wrapper.unmount()
  })

  it('stops at 30', async () => {
    const save = vi.spyOn(api, 'saveMyAiPreferences')
    const { wrapper } = await mountPanel({ prefs: { ...basePrefs, repeat_count_max: 30, repeat_count_max_effective: 30 } })
    expect((wrapper.get('[data-test="my-repeat-increase"]').element as HTMLButtonElement).disabled).toBe(true)
    await wrapper.get('[data-test="my-repeat-increase"]').trigger('click')
    await flushPromises()
    expect(save).not.toHaveBeenCalled()
    expect(value(wrapper)).toBe('30')
    wrapper.unmount()
  })

  it('goes back to the system default (null) and hides the reset while no value of my own is set', async () => {
    const save = vi.spyOn(api, 'saveMyAiPreferences').mockResolvedValue({ ...basePrefs, repeat_count_max: null, repeat_count_max_effective: 5 })
    const { wrapper } = await mountPanel({ prefs: { ...basePrefs, repeat_count_max: 2, repeat_count_max_effective: 2 } })
    const reset = wrapper.get('[data-test="my-repeat-reset"]')
    expect(reset.text()).toBe('기본값으로')
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

  it('says my values could not load when they cannot be read or are not in the contract shape', async () => {
    let { wrapper } = await mountPanel({ prefs: new Error('no route') })
    expect(wrapper.find('[data-test="my-repeat-card"]').exists()).toBe(false)
    expect(wrapper.find('[data-test="my-title-ai-card"]').exists()).toBe(false)
    expect(wrapper.get('[data-test="repeat-row"]').text()).toContain('불러오지 못했습니다')
    wrapper.unmount()
    ;({ wrapper } = await mountPanel({ prefs: { settings: {} } as never }))
    expect(wrapper.find('[data-test="my-repeat-card"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('tells an administrator too that my values could not load, with no system editor in their place', async () => {
    const r = await mountPanel({ admin: true, prefs: new Error('no route') })
    expect(r.fetchPrefs).toHaveBeenCalled()
    expect(r.wrapper.find('[data-test="my-repeat-card"]').exists()).toBe(false)
    expect(r.wrapper.find('[data-test="my-title-ai-card"]').exists()).toBe(false)
    expect(r.wrapper.get('[data-test="title-row"]').text()).toContain('불러오지 못했습니다')
    expect(r.wrapper.find('[data-test="system-repeat-card"]').exists()).toBe(false)
    r.wrapper.unmount()
  })

  it('names the new choices in English and Japanese', async () => {
    i18n.global.locale.value = 'en'
    let { wrapper } = await mountPanel()
    expect(wrapper.findAll('[data-test="my-title-ai-select"] option').map((o) => o.text()).slice(0, 2)).toEqual(['Same AI that answered', 'Off'])
    wrapper.unmount()
    i18n.global.locale.value = 'ja'
    ;({ wrapper } = await mountPanel())
    expect(wrapper.findAll('[data-test="my-title-ai-select"] option').map((o) => o.text()).slice(0, 2)).toEqual(['回答したAIのまま', 'オフ'])
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

